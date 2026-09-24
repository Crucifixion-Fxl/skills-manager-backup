#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Bind a Buzz channel to a Feishu group, keep the members aligned, and mirror
messages both ways with the owner's local CLIs (engineering/skills#110).

Design: references/feishu-group-sync.md. Identities, by construction and
checked at the start of every round:

- Feishu -> Buzz is sent with the channel's mirror identity only (its 0600 env
  file, whose key must resolve to the configured mirror pubkey); the parent's
  Buzz key and tokens never reach a child process.
- A human's Buzz message goes to Feishu through the Channel's configured Desk bot;
  a configured agent's message uses that agent's own verified bot. The Desk also
  relays permitted context authors and remote agents. By default (config
  message_format "card") a message is a card:
  the speaker as the title, markdown, a folded full text, a button back to Buzz;
  "text" is the plain message signed "<name>（Buzz）：". A card Feishu refuses for
  its content is said as text instead (never when the outcome is unknown: that
  would double it).
- Membership changes use the owner's user token; a new group's owner is the
  owner.

Strangers: by default a Feishu message whose sender maps to no channel member is mirrored as
labelled context. Config feishu_unmapped_senders defaults to "context" (explicit "skip" drops it) and mirrors it as words
to read (ADR-0016): signed "[飞书·非成员] <name>：", with images on the member media path, and only for a sender Feishu vouched
for who maps to nobody; excluded with feishu_sender_allowlist. He can still wake an agent — a
selected mention of one of the channel's bots becomes a real one, the same as a member's — but
never a person: typed @ and nostr:, and any mention of a human, are neutralised or dropped.

Mentions: Feishu -> Buzz only honours selected mention entities that resolve to
current channel members; Buzz -> Feishu can mention a human by verified email
in a Desk-sent card. The owner's open_ids cannot be used by the Desk because
open_ids are per app. Unknown mention targets become plain @name; a union_id is
refused by Feishu in a card, so it is never used there. Bodies and user-set
names are neutralised so text alone can neither notify nor forge a signed line.

People: who a Buzz key is in Feishu is read every round from the bridge's signed
endpoint (infra/buzz-deploy ADR-0015), because bindings change. The owner's key
signs that one GET (NIP-98) and is never handed to a child process. An open_id
belongs to one Feishu app, so the bridge's open_ids say nothing in the owner's app;
config `identity` chooses what is shared instead: "union_id" (default; the same in
every app of the tenant, from the answer's union_ids) or "email" (each member's
address, turned into this app's open_id with a search that must match exactly).
Whichever it is, a person that cannot be vouched for is unmapped, never guessed.

Nothing here prints emails, keys or tokens; the state keeps ids, pubkeys and
cursors only.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import functools
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import gitlab_buzz_sync as sync  # noqa: E402  (neutralize, validate_buzz_cli_path)

_HELD = object()  # a reply that waits for its thread root (Round._thread_root_parent)
PENDING = "pending"  # "pending:<first attempt>": sent or maybe sent, outcome unknown
RETRY = "retry"  # "retry:<last refusal>": refused, nothing sent, retried next round
FAILED = "failed"
UNKNOWN = "unknown"  # terminal: maybe delivered, never resent
STATE_FILE = "state.json"
LOCK_FILE = "round.lock"
CREATE_INTENT_SUFFIX = ".create-intent"  # next to the config while create-chat is in flight

MAX_BOTS_PER_CHAT = 15
USERS_PER_REQUEST = 50
BOTS_PER_REQUEST = 5
EDIT_KIND = 40003
MESSAGE_KINDS = "9,45001,45003"
MIRROR_KINDS = MESSAGE_KINDS + ",40003"
REACTION_KINDS = "7,5"  # a reaction and its withdrawal (NIP-09 deletion)
REACTIONS_PER_ROUND = 50  # Feishu calls per round; the rest waits for the next one
REMOVED = "removed"  # r2f: the Feishu reaction is gone (or was never made) and stays that way
LEGACY_OWNER_REACTION = "owner"  # read-only marker: old reactions cannot be withdrawn through a new sender
# What an agent's Buzz reaction becomes in Feishu. Every value is an emoji_type from lark-cli's list
# (lark-im-reactions.md). Anything else is skipped and counted; config `reaction_map` extends or overrides.
DEFAULT_REACTION_MAP: dict[str, str] = {
    "👀": "GLANCE", "💬": "Typing", "✅": "DONE", "👍": "THUMBSUP", "+": "THUMBSUP", "👌": "OK", "🙏": "THANKS",
    "💪": "MUSCLE", "❌": "CrossMark",  # ✅ / ❌ are also how an agent's join request is answered (ADR-0018 / ADR-0020)
}
# Images. Feishu takes at most 10 MB per image, in these formats; Buzz's relay refuses media that carries metadata, so only
# the formats whose metadata this script can strip go the other way.
IMAGES_PER_EVENT = 9  # per Buzz event / Feishu message; the rest is counted, not sent
IMAGE_MAX_BYTES = 10 * 1024 * 1024
IMAGE_FEISHU_FORMATS = frozenset({"jpeg", "png", "gif", "webp", "bmp", "tiff"})
IMAGE_BUZZ_FORMATS = frozenset({"jpeg", "png", "gif", "webp"})
IMAGE_EXTENSIONS = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp", "bmp": ".bmp", "tiff": ".tiff"}
SKIPPED = "skipped"  # images ledger, terminal: deliberately not sent (policy), counted once in the report
# What `buzz messages send --file` writes into imeta (实测): <relay>/media/<sha256>[.ext]. Anything else is not fetched.
MEDIA_URL_RE = re.compile(r"https?://[^/?#\s]+/media/([0-9a-f]{64})(\.[A-Za-z0-9]{1,5})?")
MARKDOWN_IMAGE_RE = re.compile(r'!\[([^\]\n]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)')
# Feishu's own rendering of an image (实测 lark-cli): an image message is `[Image: img_…]`, a rich-text post has `![Image](img_…)`.
FEISHU_IMAGE_MARK_RE = re.compile(r"\[Image: (img_[A-Za-z0-9_-]{1,120})\]|!\[[^\]\n]*\]\((img_[A-Za-z0-9_-]{1,120})\)")
FEISHU_IMAGE_KEY_RE = re.compile(r"img_[A-Za-z0-9_-]{1,120}")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_DRAWING_CHUNKS = frozenset({b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"acTL", b"fcTL", b"fdAT"})
WEBP_KEPT_CHUNKS = frozenset({b"VP8X", b"VP8 ", b"VP8L", b"ALPH", b"ANIM", b"ANMF"})
WEBP_METADATA_FLAGS = 0x20 | 0x08 | 0x04  # VP8X: ICC profile, EXIF, XMP
GIF_KEPT_APPLICATIONS = frozenset({b"NETSCAPE2.0", b"ANIMEXTS1.0"})  # the animation loop count, not data about the file
MEDIA_SEGMENT_RE = re.compile(r"[0-9a-f]{64}(\.[A-Za-z0-9]{1,5})?")  # the only argument `buzz media get` is ever given
BMP_DIB_HEADER_SIZES = frozenset({12, 40, 52, 56, 64, 108, 124})
BUZZ_PAGE_LIMIT = 200  # `messages get` clamps --limit to 200 and returns the newest first
BUZZ_PAGE_MAX = 50
BUZZ_OVERLAP_SECONDS = 900  # the relay accepts created_at within ±900s of its clock
FEISHU_OVERLAP_SECONDS = 120  # Feishu create_time has minute resolution
THREAD_DISCOVERY_SECONDS = 6 * 3600  # roots that get a new thread are found within this window
THREAD_HOT = 10  # the most active threads, polled every round
THREAD_ROTATE = 10  # plus the least recently polled ones, so every thread gets its turn
THREAD_KEEP = 200
THREAD_MAX_AGE_SECONDS = 7 * 86400
STALE_AFTER_SECONDS = 600
LEDGER_KEEP = 20000
IDMAP_KEEP = 5000  # the id caches: people, not messages
EMAIL_MISS_RECHECK_SECONDS = 600  # an address Feishu could not place is asked about again after this long
EMAIL_LOOKUPS_PER_ROUND = 50  # Feishu searches per round; the rest waits (and is unmapped) until the next one
MAX_SEND_ATTEMPTS = 3
FEISHU_RETRY_WINDOW_SECONDS = 2700  # the idempotency key dedupes for about an hour; keep margin for a long round
THREAD_ROOT_LOOKUPS_PER_ROUND = 20  # threads read from Buzz per round to find a root; the replies left over wait for the next round
THREAD_PAGE_LIMIT = 10  # a thread is read newest first; more new replies than this is reported
BULK_REMOVAL_LIMIT = 10
PROFILE_BATCH = 50
FEISHU_PAGE_LIMIT = 40  # pages of 50 for the mirror window; more than that fails the round
DISCOVERY_PAGE_LIMIT = 10  # newest 500 messages of the discovery window, for thread roots only
FEISHU_TIME_FORMAT = "%Y-%m-%d %H:%M"
EMOJI_TYPE_RE = re.compile(r"[A-Za-z0-9_]{1,40}")
EMOJI_KEY_MAX = 16
EMOJI_SELECTORS = "\ufe0e\ufe0f"  # text / emoji presentation: clients add them inconsistently

HEX64_RE = re.compile(r"[0-9a-f]{64}")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
OPEN_ID_RE = re.compile(r"ou_[0-9A-Za-z_]+")
UNION_ID_RE = re.compile(r"on_[0-9A-Za-z_]+")
# The bridge's emails are lower-case and deduplicated (the canonical form, see normalize_email); a shape check only,
# never a fuzzy match.
EMAIL_RE = re.compile(r"[^\s@<>()\[\]\\\"',;:\x00-\x1f\x7fA-Z]{1,64}@[^\s@<>()\[\]\\\"',;:\x00-\x1f\x7fA-Z]{1,253}")
EMAIL_MAX_LENGTH = 254
EMAILS_PER_PERSON = 20
APP_ID_RE = re.compile(r"cli_[0-9A-Za-z_]+")
CHAT_ID_RE = re.compile(r"oc_[0-9A-Za-z_]+")
MESSAGE_ID_RE = re.compile(r"om_[0-9A-Za-z_]+")
# Feishu parses <at …> in text as a real mention; a mirrored body must not carry one.
FEISHU_AT_MARKUP_RE = re.compile(r"<(\s*/?\s*at\b)", re.IGNORECASE)
BIDI_CONTROLS_RE = re.compile("[‪-‮⁦-⁩]")
# A later line of a body that looks like the other side's signature gets a visible mark. Lines
# are matched after removing format characters and NFKC (fullwidth -> ASCII).
FORGED_BUZZ_SIGNATURE_RE = re.compile(r"^\s*\[\s*飞书\s*\]")
# A stranger's body is judged more strictly: after the markdown dressing at the start of a line is taken off (see
# _context_signature_probe), a line that reads as "[飞书]" or "[飞书·非成员]" — square or 【】 brackets, spaces between the
# characters, any of the middle dots — is a forged signature. A member's body keeps the original test (default behaviour).
FORGED_CONTEXT_SIGNATURE_RE = re.compile(r"^[\[【]\s*飞\s*书\s*(?:[^\w\]】\s]{1,2}\s*非\s*成\s*员\s*)?[\]】]")
# What may stand in front of such a line without changing what a reader sees: markup and bullets (anything that is neither a word
# character nor a bracket, and the underscore), list numbers ("1." / "1)"), a task-list box ("[ ]" / "[x]").
CONTEXT_LINE_DRESSING_RE = re.compile(r"^(?:_|[^\w\[【]|\d{1,3}[.)]|\[[ xX]\])+")
LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")
FORGED_FEISHU_SIGNATURE_RE = re.compile(r"\(\s*buzz\s*\)\s*:", re.IGNORECASE)
# Blank-looking letters and symbols that are not format characters.
INVISIBLE_FILLERS = frozenset("\u115f\u1160\u3164\uffa0\u2800\u180e")
# Only these lark-cli error types mean the server refused the call; anything else (e.g. network)
# may have happened after the request was delivered.
LARK_DEFINITE_ERROR_TYPES = frozenset({"api", "validation", "authentication", "permission"})
# A card that Feishu refuses with one of these says the card itself is wrong (230099 among them): it did not go out, and the same
# message is said as text instead. A rate limit is an api error too but says nothing about the card, so it is not among the reasons.
CARD_REFUSAL_TYPES = frozenset({"api", "validation"})
LARK_RATE_LIMIT_CODES = frozenset({230020, 11232, 99991400})
TEXT_FALLBACK_KEY_SUFFIX = "-text"  # the idempotency key of the text a refused card falls back to
# The b2f ledger extra of a Buzz -> Feishu attempt is "<parent>[,<mode>]": what the first attempt was, which every retry repeats
# exactly (same request under the same idempotency key, whatever message_format says by then). No mode: plain text, the way
# text mode (and every version before cards) writes it.
SEND_CARD = "card"  # the first attempt is a card
SEND_TEXT_FALLBACK = "text"  # a refused card is being said as text, under the key with TEXT_FALLBACK_KEY_SUFFIX
LINE_BREAK_RE = re.compile("(\r\n|[\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029])")
CONTINUATION_MARK = "↳ "
NAME_TRANSLATION = str.maketrans({"<": "＜", ">": "＞", '"': "＂"})

BUZZ_ENV_KEYS = ("BUZZ_PRIVATE_KEY", "BUZZ_RELAY_URL", "BUZZ_AUTH_TAG")
# No D-Bus / runtime dir: lark-cli must keep secrets in its per-profile file keychain.
CHILD_ENV_KEYS = ("HOME", "PATH", "LANG", "LC_ALL", "TZ", "XDG_CONFIG_HOME", "XDG_DATA_HOME")
# lark-cli renders create_time in its own timezone; pin it so the cursors are UTC.
LARK_ENV = {"LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1", "TZ": "UTC"}

# Cards (Buzz -> Feishu), kept short for a phone: title, one grey byline with a link, the mentions, the folded full text. Feishu
# refuses a card of MAX_CARD_BYTES or more, so the folded text is cut once the card passes CARD_TRIM_BYTES.
CARD_SCHEMA = "2.0"
MAX_CARD_BYTES = 30 * 1024
CARD_TRIM_BYTES = 28 * 1024
CARD_SUMMARY_CHARS = 60
CARD_NAME_CHARS = 60
CARD_TITLE_COLUMNS = 72  # let the title wrap naturally to about two phone lines: a wide (CJK, emoji) character counts 2, any other 1 - about 36 Chinese characters
CARD_NOTIFY_HEADER = "[gitlab-notify:v1]"  # a GitLab -> Buzz sync message's machine header (a whole line): it never reaches a card
COMPACT_EDIT_REV_RE = re.compile(r"\[rev:([1-9][0-9]{0,8})\](?:\[route:skip\])?$")
CARD_NOTIFIED_PREFIX = "🔔 通知 "  # the line naming who a sync message notified (gitlab_buzz_sync.NOTIFIED_PREFIX, ADR-0012): a card has its own @ line
CARD_LEGACY_TITLE_RE = re.compile(r"title: (?=\S)(.*)")  # the second line of a legacy `key: value` sync message (header first): its title
CARD_MENTIONS_MAX = 20
CARD_OPEN_PATH = "/bind/open"
CARD_OPEN_TEXT = "在 Buzz 中打开"
CARD_EXPAND = "展开全文（{n} 字）"
CARD_TRUNCATED_NOTE = "（内容过长已截断，完整内容请在 Buzz 中打开）"
CARD_STATUS_HISTORY_TITLE = "状态记录"
CARD_STATUS_TRUNCATED_NOTE = "（记录过长，仅显示最近部分；完整内容请在 Buzz 中打开）"
CARD_GITLAB_OPEN_TEXT = "在 GitLab 中打开"
CARD_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
CARD_CONTROL_RE = re.compile("[\x00-\x08\x0e-\x1b\x1f\x7f]")  # what is left once every line break became \n (tab stays)
CARD_LINK_TEXT_OPEN_RE = re.compile(r"!?\[[^\[\]]*$")  # [text ...            (the link text is still open)
CARD_LINK_URL_OPEN_RE = re.compile(r"!?\[[^\[\]]*\]\([^)]*$")  # [text](url ...   (the address is still open)
CARD_LINK_TEXT_CLOSED_RE = re.compile(r"!?\[[^\[\]]*\]$")  # [text]             (and "(" is next)
CARD_BARE_URL_RE = re.compile(r"(?:https?://|www\.)\S*$")
CARD_TITLE_LINK_RE = re.compile(r"!?\[([^\[\]]*)\]\([^)]*\)")  # [text](url) and ![alt](url): the title keeps the text (run on a line whose escapes are masked)
CARD_ESCAPE_RE = re.compile(r"\\([\\\[\]])")  # a backslash, `[` or `]` behind a backslash: how gitlab_buzz_sync._md_escape writes them in a title (a text, never syntax)
CARD_TITLE_BLOCK_RE = re.compile(r"^(?:>\s?)*(?:#{1,6}\s+)?(?:(?:[-*+]|\d{1,3}[.)])\s+)?(?:\[[ xX]\]\s+)?")  # quote, heading, list, task
CARD_TITLE_RULE_RE = re.compile(r"(?:[-*_=]\s*){3,}")  # a horizontal rule (or the underline of a heading)
CARD_TITLE_STRONG_RE = re.compile(r"(\*\*|__|~~)(?=\S)(.+?)(?<=\S)\1")
CARD_TITLE_EMPHASIS_RE = re.compile(r"(?<![\w*])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*])")
BUZZ_SCHEME_RE = re.compile(r"buzz://", re.IGNORECASE)

CONFIG_KEYS = {"channel_id", "chat_id", "owner_open_id", "owner_app_id", "mirror_pubkey", "mirror_env_file",
               "people_api", "remove_extras", "agents", "buzz_cli", "buzz_cli_sha256", "lark_cli", "desk_pubkey"}
OPTIONAL_CONFIG_KEYS = {"reaction_map", "identity", "message_format", "feishu_sender_allowlist", "feishu_unmapped_senders",
                       "buzz_unmapped_senders", "buzz_unmanaged_agents", "membership_sync", "reaction_sync", "people_cache_file"}
# Config feishu_sender_allowlist: only these Buzz pubkeys' Feishu messages are mirrored into Buzz (absent = everyone
# who is mapped and in the channel, as before). At most this many distinct pubkeys.
MAX_SENDER_ALLOWLIST = 50
# Config feishu_unmapped_senders: what becomes of a Feishu message whose sender is a known person that maps to no channel
# member. "context" (the default) mirrors it as words and images to read; "skip" explicitly drops it (see mirror docs).
UNMAPPED_SENDER_MODES = ("skip", "context")
DEFAULT_UNMAPPED_SENDERS = "context"
CONTEXT_SENDER_LABEL = "[飞书·非成员]"
CONTEXT_SENDER_FALLBACK_NAME = "飞书用户"  # when the display name is empty once cleaned
CONTEXT_NAME_LIMIT = 60  # characters of a stranger's display name that are mirrored (he sets it himself)
CONTEXT_BODY_LIMIT = 4000  # characters of a stranger's body that are mirrored; the rest is cut (the CLI refuses a huge message)
# Config buzz_unmapped_senders: the mirror image of feishu_unmapped_senders, Buzz -> Feishu. A Buzz author is always a real,
# signed identity (there is no "Feishu vouches for him" question on this side), so "context" here mirrors every author who
# is neither a verified channel human nor a configured agent — not just ones with a resolvable profile name (skills#142: a
# Workflow's own signing identity has none, and its messages must still mirror so the thread they open does not break).
# "skip" (the default) keeps the old behaviour: not_channel_human, dropped. Symmetric with ADR-0016 (skills#135): such an
# author's own mentions of one of the channel's agents still become a real <at> (he can wake it), but any mention of a
# human is silently dropped, never a real Feishu notification.
BUZZ_UNMAPPED_SENDER_MODES = ("skip", "context")
DEFAULT_BUZZ_UNMAPPED_SENDERS = "skip"
BUZZ_CONTEXT_LABEL = "非成员"  # inserted into the existing "（Buzz）" / plain speaker signature

# Config buzz_unmanaged_agents: what to do with a channel agent this host has no configuration for at all. An agent
# belongs to as many channels as its owner is pulled into, but each channel's group sync runs on a different person's
# machine, and an agent's Feishu app credentials may only live with its own owner (SKILL.md Rule 11) — so any other
# operator is permanently unable to speak as it. "relay" (the default since ADR-0019) mirrors it through the owner's app
# bot like a human's, signed distinctly so nobody takes it for the agent's own bot (skills#143). "skip" keeps the old
# behaviour: the message is dropped (agent_bot_unavailable) and the group never sees that agent answer. This is only ever about
# an agent absent from `agents`: one that is configured but has no live bot this round is a transient, self-healing
# state, and relaying it would make the same agent speak for itself one round and be quoted the next.
BUZZ_UNMANAGED_AGENT_MODES = ("skip", "relay")
DEFAULT_BUZZ_UNMANAGED_AGENTS = "relay"
BUZZ_AGENT_RELAY_LABEL = "助手"  # inserted the same way as BUZZ_CONTEXT_LABEL
# How a channel's people are told apart from everyone else in the group. Feishu's open_id belongs to one app, so the
# bridge's open_ids are useless here: union_id (the same for every app of the tenant) or the email are what is shared.
IDENTITY_MODES = ("union_id", "email")
DEFAULT_IDENTITY = "union_id"
# How a Buzz message is said in Feishu: a card (markdown, a folded full text, a button back to Buzz) or plain text.
MESSAGE_FORMATS = ("card", "text")
DEFAULT_MESSAGE_FORMAT = "card"
PEOPLE_API_KEYS = {"base_url", "signer_env_file"}
AGENT_KEYS = {"app_id", "lark_config_dir", "lark_data_dir"}
HUMAN_ROLES = frozenset({"owner", "admin", "member", "guest"})
MEMBER_FAILURE_LISTS = ("invalid_id_list", "not_existed_id_list", "pending_approval_id_list")

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED, EXIT_ATTENTION = 0, 1, 2, 3
AUTH_HINT = "the owner's lark-cli login may have expired: run `lark-cli auth login` as the owner"


class GroupSyncError(RuntimeError):
    report: dict[str, Any] | None = None


class BuzzBacklogError(GroupSyncError):
    """More Buzz events than BUZZ_PAGE_MAX pages: refuse rather than skip any."""


class CliError(GroupSyncError):
    """A CLI call that failed. `definite` is True only when the tool reported a
    refusal, so nothing was sent; otherwise the outcome is unknown."""

    def __init__(self, what: str, code: int, kind: str = "", *, definite: bool, error_type: str = "") -> None:
        super().__init__(f"{what} failed (code {code}) {kind}".strip())
        self.code = code
        self.kind = kind
        self.definite = definite
        self.error_type = error_type  # lark-cli's error `type` (api, validation, network ...); "" when there is none


@dataclass(frozen=True)
class Preflight:
    ok: bool
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    can_remove: bool = False


@dataclass(frozen=True)
class MembershipPlan:
    add_users: tuple[str, ...] = ()
    remove_users: tuple[str, ...] = ()
    add_bots: tuple[str, ...] = ()
    remove_bots: tuple[str, ...] = ()
    blocked_bots: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageRef:
    segment: str  # "<sha256>[.ext]": the relay media path `buzz media get` takes
    sha256: str
    size: int | None  # what the event claims; never trusted


class ImageSkip(Exception):
    """An image that is deliberately not mirrored (policy, not failure); `reason` is the report's name for it."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Outbound:
    event_id: str
    via_app_id: str | None  # None: the configured Desk bot
    text: str
    parent_event_id: str | None
    card: str | None = None  # the card's JSON when the message is said as a card; `text` then stands ready as its fallback
    images: tuple[ImageRef | str, ...] = ()  # in imeta order; a str is a skip reason for that attachment
    images_over: int = 0  # attachments beyond IMAGES_PER_EVENT
    relayed: bool = False  # an agent this host cannot speak as, said through this Channel's Desk (buzz_unmanaged_agents)


@dataclass(frozen=True)
class CardContext:
    """What a card needs besides the event: where the button leads, the channel's name, and who can be named how. `emails`
    and `open_ids` only hold people the bridge vouches for (open_ids: of the owner's app) and are never stored."""
    link_base: str
    channel_id: str
    channel_name: str = ""
    emails: Mapping[str, str] = field(default_factory=dict)  # pubkey -> address
    open_ids: Mapping[str, str] = field(default_factory=dict)  # pubkey -> open_id in the owner's app


@dataclass(frozen=True)
class Inbound:
    message_id: str
    sender_pubkey: str
    text: str
    mentions: tuple[str, ...]
    image_keys: tuple[str, ...] = ()  # Feishu image keys found in the message, in order, without duplicates
    context_only: bool = False  # sender maps to no channel member: labelled context; selected agent mentions and images survive


@dataclass
class State:
    binding: str = ""  # "<channel_id>|<chat_id>"; a state dir serves one binding
    floor: int = 0  # binding start: nothing older is ever mirrored
    buzz_since: int = 0
    feishu_since: int = 0
    buzz_floor: int = 0  # set when the owner drops a Buzz backlog: nothing older is read again
    feishu_floor: int = 0  # the same for a Feishu backlog
    b2f: dict[str, str] = field(default_factory=dict)  # Buzz event id -> Feishu message id | pending:<t> | failed
    b2f_modes: dict[str, str] = field(default_factory=dict)  # mirrored Buzz event id -> card | text
    b2f_senders: dict[str, str] = field(default_factory=dict)  # app that first attempted this Buzz event; stable across retries/edits/images
    e2f: dict[str, str] = field(default_factory=dict)  # Buzz edit id -> Feishu message id | pending:<t>:<target>|<message>|<mode> | failed
    edit_unresolved: dict[str, int] = field(default_factory=dict)  # edit id while pending or retry -> created_at
    f2b: dict[str, str] = field(default_factory=dict)  # Feishu message id -> Buzz event id | pending:<t> | failed
    attempts: dict[str, int] = field(default_factory=dict)  # refused sends per id
    threads: dict[str, int] = field(default_factory=dict)  # Feishu root message id -> last activity
    polled: dict[str, int] = field(default_factory=dict)  # Feishu root message id -> last complete poll (its cursor)
    tried: dict[str, int] = field(default_factory=dict)  # Feishu root message id -> last failed or partial poll
    unresolved: dict[str, int] = field(default_factory=dict)  # Buzz event id -> created_at, pending or retry
    f_unresolved: dict[str, int] = field(default_factory=dict)  # Feishu message id -> created, retry
    r2f: dict[str, str] = field(default_factory=dict)  # Buzz reaction id -> <agent pubkey>|<message id>|<emoji_type>|<reaction id> | failed | removed
    react_since: int = 0  # the reaction read cursor (kinds 7 and 5), own so a capped round can hold it back
    idmap: dict[str, str] = field(default_factory=dict)  # union mode: this app's open_id -> union_id, as paired by Feishu itself
    emailmap: dict[str, str] = field(default_factory=dict)  # email mode: sha256(app id, email) -> open_id | miss:<time>
    images: dict[str, str] = field(default_factory=dict)  # "<Buzz event id>:<n>" -> Feishu message id | pending:<t>:<parent> | retry:<t>:<parent> | failed | unknown | skipped
    img_unresolved: dict[str, int] = field(default_factory=dict)  # the same keys while pending or retry -> the event's created_at
    # Two-way members (ADR-0020). A Feishu key is "u:<union_id>" (union mode), "o:<owner app id>:<open_id>" (email mode) or
    # "b:<app id>" (a bot).
    members_synced: int = 0  # when the two-way snapshots were first taken; 0: never, and the next round only records them
    feishu_seen: dict[str, str] = field(default_factory=dict)  # the group's members at the round's end: Feishu key -> "" (a set)
    buzz_seen: dict[str, str] = field(default_factory=dict)  # channel member pubkey -> its Feishu key ("" unknown), at the round's end
    member_notes: dict[str, int] = field(default_factory=dict)  # "<what>:<who>" -> when: a group notice given, or a refusal remembered
    # pending 9000/9001 operation -> the exact signed NIP-01 event; retries reuse its id, signature and body
    member_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    member_event_stream: str = ""  # random persistent producer id; survives a legitimate signer rotation
    member_event_seq: int = 0  # persisted high-water mark; orders this stream's operations inside one relay clock window
    member_event_blocks: dict[str, str] = field(default_factory=dict)  # pending operation -> persistent retry stop reason
    member_notice_event: str = ""  # canonical Buzz kind 9; later membership-status rounds edit this event
    member_notice_feishu: str = ""  # direct-Feishu fallback, later attached to member_notice_event in b2f
    member_notice_sender: str = ""  # sender app of the direct fallback; empty in old state means owner app
    member_notice_content: str = ""  # latest text, retained until a fallback can be backfilled to Buzz
    member_notice_active: bool = False  # last status was a failure; the first healthy round publishes recovery
    people_seen: dict[str, str] = field(default_factory=dict)  # Feishu user key -> "<pubkey>|<last seen>": this channel's members, past and present
    # One introduction per binding and agent. Existing agents are recorded as "baseline" on the first round after upgrade;
    # a new join is pending/retried with one stable Feishu idempotency key, then stores the message id.
    agent_intros_initialized: bool = False
    agent_intros: dict[str, str] = field(default_factory=dict)  # agent pubkey -> baseline | pending/retry marker | message id | terminal
    agent_intro_senders: dict[str, str] = field(default_factory=dict)  # app that first attempted a proxied introduction
    # Two-way reactions (ADR-0020).
    rwatch: dict[str, str] = field(default_factory=dict)  # Feishu message id -> "<Buzz event id>|<watched until>"
    f2r: dict[str, str] = field(default_factory=dict)  # "<message>|<operator>|<emoji_type>" -> the mirror's kind 7 id | pending:<t> | failed | skipped


# ---------------------------------------------------------------- files


def _read_owner_only(path: Path, what: str) -> str:
    """Read a regular, non-symlink file readable by its owner only."""
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077 or meta.st_uid != os.getuid():
            raise GroupSyncError(f"{what} must be a regular 0600 file owned by the current user")
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = -1
            return fh.read()
    except OSError:
        raise GroupSyncError(f"{what} could not be read as an owner-only regular file") from None
    finally:
        if fd >= 0:
            os.close(fd)


def _write_owner_only(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise GroupSyncError(f"{what} is not valid JSON") from None


# ---------------------------------------------------------------- people


# The bridge's people endpoint (infra/buzz-deploy ADR-0015). The channel's owner or an admin signs one GET with
# their own Buzz key (NIP-98) and gets {pubkey: open_id} for the channel's live members that have a verified
# binding. It is read every round: bindings change (a new person binds, someone unbinds, someone leaves).
PEOPLE_API_MAX_BYTES = 1 << 20
PEOPLE_API_TIMEOUT = 15.0
NIP98_KIND = 27235


@functools.lru_cache(maxsize=1)
def _signer_pubkey(secret_key_hex: str) -> str:
    """The public key of a private key, derived once per process (it costs a curve multiplication)."""
    return sync.nk.pubkey_xonly(bytes.fromhex(secret_key_hex)).hex()


def nip98_header(secret_key_hex: str, method: str, url: str, now: datetime, aux: bytes | None = None,
                 body: bytes | None = None) -> str:
    """The Authorization header of one request: "Nostr " + padded base64 of a kind 27235 event that names this
    URL and method. `aux` is BIP-340's nonce randomness (fresh random bytes unless a test pins it). With a `body`
    (a POST to the relay) the event also names it (`payload`, its sha256, which the relay checks) and carries a
    random `nonce`, so two identical requests in the same second are two events and the relay's replay check lets
    both through; without one the tags are exactly u and method, all the bridge's strict verifier accepts."""
    if not isinstance(secret_key_hex, str) or not HEX64_RE.fullmatch(secret_key_hex):
        raise GroupSyncError("the signing key is not 64 lower-case hex digits")
    try:
        secret = bytes.fromhex(secret_key_hex)
        pubkey = _signer_pubkey(secret_key_hex)
    except ValueError:
        raise GroupSyncError("the signing key is not a valid secp256k1 key") from None
    created = int(now.timestamp())
    tags = [["u", url], ["method", method.upper()]]
    if body is not None:
        tags += [["payload", hashlib.sha256(body).hexdigest()], ["nonce", secrets.token_hex(16)]]
    serial = json.dumps([0, pubkey, created, NIP98_KIND, tags, ""], separators=(",", ":"), ensure_ascii=False)
    event_id = hashlib.sha256(serial.encode()).hexdigest()
    sig = sync.nk.schnorr_sign(bytes.fromhex(event_id), secret, secrets.token_bytes(32) if aux is None else aux).hex()
    event = {"id": event_id, "pubkey": pubkey, "created_at": created, "kind": NIP98_KIND, "tags": tags, "content": "",
             "sig": sig}
    return "Nostr " + base64.b64encode(json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode()).decode()


def people_url(base_url: str, channel_id: str) -> str:
    if not UUID_RE.fullmatch(channel_id):
        raise GroupSyncError("channel id is not a lower-case uuid")
    return f"{base_url}/bind/api/channels/{channel_id}/people"


@dataclass(frozen=True)
class PeopleAnswer:
    open_ids: dict[str, str]  # the bridge app's open_ids: not this app's, never used to tell who is who
    union_ids: dict[str, str] | None = None  # None: the answer has no such field
    emails: dict[str, list[str]] | None = None


def identity_mode(cfg: Mapping[str, Any]) -> str:
    return cfg.get("identity", DEFAULT_IDENTITY)


def message_format(cfg: Mapping[str, Any]) -> str:
    return cfg.get("message_format", DEFAULT_MESSAGE_FORMAT)


def sender_allowlist(cfg: Mapping[str, Any]) -> frozenset[str] | None:
    """Whose Feishu messages may be mirrored into Buzz; None when the config sets no list (no restriction)."""
    listed = cfg.get("feishu_sender_allowlist")
    return None if listed is None else frozenset(listed)


def unmapped_sender_mode(cfg: Mapping[str, Any]) -> str:
    """What to do with a message whose sender is a known person that maps to no channel member: "context" (the default,
    and what an absent key means) mirrors it as labelled context; "skip" explicitly drops it. An existing sender allowlist
    is itself an explicit restriction and therefore implies "skip" when this key is absent (see route_feishu_message)."""
    if "feishu_unmapped_senders" not in cfg and "feishu_sender_allowlist" in cfg:
        return "skip"
    return cfg.get("feishu_unmapped_senders", DEFAULT_UNMAPPED_SENDERS)


def buzz_unmapped_sender_mode(cfg: Mapping[str, Any]) -> str:
    """The Buzz -> Feishu mirror of unmapped_sender_mode: what to do with a Buzz author who is neither a verified channel
    human nor a configured agent. "skip" (the default, and what an absent key means) drops it (not_channel_human);
    "context" mirrors it (see route_buzz_event)."""
    return cfg.get("buzz_unmapped_senders", DEFAULT_BUZZ_UNMAPPED_SENDERS)


def buzz_unmanaged_agent_mode(cfg: Mapping[str, Any]) -> str:
    """What to do with a channel agent this host has no configuration for: "relay" (the default, and what an absent key
    means, ADR-0019) mirrors it through the owner's app bot (see route_buzz_event); "skip" drops it
    (agent_bot_unavailable, the old default)."""
    return cfg.get("buzz_unmanaged_agents", DEFAULT_BUZZ_UNMANAGED_AGENTS)


def one_pubkey_per_id(ids: Mapping[str, str]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Invert {pubkey: id}: an id that exactly one pubkey has is {id: pubkey}; an id that two or more pubkeys have (the
    bridge bound them to the same Feishu account) tells nothing about who is who, so it is left out of the first result
    and listed, with its pubkeys, in the second. Nobody is picked, whatever the order."""
    by_id: dict[str, list[str]] = {}
    for pubkey in sorted(ids):
        by_id.setdefault(ids[pubkey], []).append(pubkey)
    return ({i: pks[0] for i, pks in by_id.items() if len(pks) == 1},
            {i: pks for i, pks in by_id.items() if len(pks) > 1})


def pick_open_id(email: str, users: list[Any]) -> str | None:
    """contact +search-user is a fuzzy search, so its answer is not believed as such: exactly one open_id whose email or
    enterprise_email is, character for character, the address asked about. A near miss (prefix, suffix, another case),
    an external-tenant account, a malformed row, or two different accounts with the address: nobody."""
    if not email:
        return None
    hits = set()
    for user in users:
        if not isinstance(user, dict) or user.get("is_cross_tenant"):
            continue
        open_id = user.get("open_id")
        if isinstance(open_id, str) and OPEN_ID_RE.fullmatch(open_id) and email in (user.get("email"), user.get("enterprise_email")):
            hits.add(open_id)
    return hits.pop() if len(hits) == 1 else None


def normalize_email(value: str) -> str:
    """The canonical form of an address from the bridge: the contract says lower-case, so spaces around it and a
    different case are tolerated (gitlab_buzz_people_generate reads the same answer the same way) and removed."""
    return value.strip(" ").lower()


def _email_ok(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    canonical = normalize_email(value)
    return len(canonical) <= EMAIL_MAX_LENGTH and EMAIL_RE.fullmatch(canonical) is not None


def _keyed(doc: Mapping[str, Any], name: str, value_ok: Callable[[Any], bool]) -> dict[str, Any] | None:
    """An optional {64 hex key: value} object of the answer: None when absent, strict when present."""
    if name not in doc:
        return None
    mapping = doc[name]
    if not isinstance(mapping, dict):
        raise GroupSyncError(f"people API answer has a malformed {name} object")
    for key, value in mapping.items():
        if not HEX64_RE.fullmatch(key) or not value_ok(value):
            raise GroupSyncError(f"people API answer has a malformed entry in {name}")
    return dict(mapping)


def parse_people_response(body: bytes, channel_id: str) -> PeopleAnswer:
    """The endpoint's answer, strictly: the channel that was asked for, {64 hex key: open_id} (the bridge app's, kept
    for the contract but never used to identify anyone) and the optional union_ids ({key: on_...}) and emails
    ({key: [address, ...]}). Nothing of the body is quoted in an error. A later extra top-level field is tolerated;
    a malformed person is not, whichever mode is chosen."""
    if len(body) > PEOPLE_API_MAX_BYTES:
        raise GroupSyncError("people API answer is larger than the cap")
    try:
        doc = json.loads(body)
    except ValueError:  # includes UnicodeDecodeError
        raise GroupSyncError("people API answer is not JSON") from None
    if not isinstance(doc, dict) or doc.get("channel") != channel_id:
        raise GroupSyncError("people API answer is not for this channel")
    people = doc.get("people")
    if not isinstance(people, dict):
        raise GroupSyncError("people API answer has no people object")
    for key, open_id in people.items():
        if not HEX64_RE.fullmatch(key) or not isinstance(open_id, str) or not OPEN_ID_RE.fullmatch(open_id):
            raise GroupSyncError("people API answer has a malformed person")
    union_ids = _keyed(doc, "union_ids", lambda v: isinstance(v, str) and UNION_ID_RE.fullmatch(v) is not None)
    emails = _keyed(doc, "emails", lambda v: isinstance(v, list) and 0 < len(v) <= EMAILS_PER_PERSON
                    and all(_email_ok(e) for e in v))
    if emails is not None:
        emails = {pubkey: [normalize_email(e) for e in addresses] for pubkey, addresses in emails.items()}
    return PeopleAnswer(open_ids=dict(people), union_ids=union_ids, emails=emails)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, not something to chase: the signature names one URL."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _http_get(url: str, headers: Mapping[str, str], timeout: float, body: bytes | None = None) -> tuple[int, bytes]:
    """One GET (a POST of `body` when there is one): (status, body) for any HTTP answer, 3xx included and not
    followed. A transport failure is an OSError; an answer over the cap or a URL that is not http(s) is a
    GroupSyncError. No proxy is used."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise GroupSyncError("the people API address must be http(s)")
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="GET" if body is None else "POST")
    try:
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect).open(request, timeout=timeout) as response:
                status, body = response.status, response.read(PEOPLE_API_MAX_BYTES + 1)
        except urllib.error.HTTPError as answer:  # 3xx (not followed), 4xx, 5xx
            status, body = answer.code, answer.read(PEOPLE_API_MAX_BYTES + 1)
            answer.close()
    except http.client.HTTPException as exc:
        raise OSError(f"HTTP transport error: {type(exc).__name__}") from None
    if len(body) > PEOPLE_API_MAX_BYTES:
        raise GroupSyncError("people API answer is larger than the cap")
    return status, body


def load_signer_key(path: Path) -> str:
    """The owner's Buzz key (BUZZ_PRIVATE_KEY, hex or nsec) from a 0600 env file, as 64 lower-case hex digits.
    It is used to sign the people request and nothing else; no error quotes it."""
    value = ""
    for raw in _read_owner_only(Path(path), "signer env file").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, sep, rest = line.partition("=")
        if sep and name.strip() == "BUZZ_PRIVATE_KEY":
            value = rest.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
    return secret_hex(value, "signer env file")


def secret_hex(value: str, what: str) -> str:
    """A BUZZ_PRIVATE_KEY value (hex or nsec) as 64 lower-case hex digits; no error quotes it."""
    if value.startswith("nsec1"):
        try:
            value = sync.nk.bech32_decode(value, "nsec").hex()
        except Exception:  # a malformed nsec: whatever the decoder raised, do not echo the key
            raise GroupSyncError(f"the {what}'s BUZZ_PRIVATE_KEY is not a valid nsec") from None
    if not HEX64_RE.fullmatch(value):
        raise GroupSyncError(f"the {what} has no BUZZ_PRIVATE_KEY as hex or nsec")
    return value


def fetch_people(cfg: Mapping[str, Any], roles: Mapping[str, str], http: Any, now: datetime) -> PeopleAnswer:
    """What the bridge knows of the channel's members that have a verified binding. The endpoint only answers a
    channel owner or admin, so a signer who is neither is refused here, with a reason, instead of as an opaque 404.
    The field the chosen identity mode needs must be there. Any failure ends the round: guessing who is who would
    move real people in or out of a group."""
    api = cfg["people_api"]
    key = load_signer_key(Path(api["signer_env_file"]))
    try:
        signer = _signer_pubkey(key)
    except ValueError:
        raise GroupSyncError("the signer key is not a valid secp256k1 key") from None
    if roles.get(signer) not in ("owner", "admin"):
        raise GroupSyncError("the people API signer is not an owner or admin of the channel")
    url = people_url(api["base_url"], cfg["channel_id"])
    headers = {"Authorization": nip98_header(key, "GET", url, now), "Accept": "application/json"}
    try:
        status, body = http(url, headers, PEOPLE_API_TIMEOUT)
    except OSError:
        raise GroupSyncError("the people API could not be reached") from None
    if status != 200:
        raise GroupSyncError(f"the people API answered HTTP {status}")
    answer = parse_people_response(body, cfg["channel_id"])
    if identity_mode(cfg) == "union_id" and answer.union_ids is None:
        raise GroupSyncError("the people API answer has no union_ids: the bridge has not deployed the union_id "
                             "backfill yet (or use identity email)")
    if identity_mode(cfg) == "email" and answer.emails is None:
        raise GroupSyncError("the people API answer has no emails: the bridge has not turned on "
                             "CHANNEL_PEOPLE_EMAILS_ENABLED (or use identity union_id)")
    return answer


# ---------------------------------------------------------------- the agent directory (ADR-0019)


# An agent's owner publishes its Feishu app id in the agent's kind:30177 ("feishu": {"app_id": ...}): world-readable on
# the relay and signed by the owner. Every round in which the channel has bot members this host has no configuration for,
# their profiles and policies are read with one signed POST /query, so the operator can pull their bots into the group and
# turn a mention of one into a real one both ways. The checks are deliberately light (the PO's call, ADR-0019): the policy
# must be signed by the owner the agent's latest profile declares (the rule Buzz Desktop applies), and a claim that is
# malformed, contested or on an app this host already uses is dropped. A failed lookup only costs the directory this round.
KIND_MANAGED_AGENT = 30177
DIRECTORY_TIMEOUT = 15.0


@dataclass(frozen=True)
class AgentIntroduction:
    """The deliberately public subset used in a group introduction.

    `description` is the agent-signed kind:0 `about`; `respond_to` is the owner-signed kind:30177 policy. Internal prompts,
    instructions and local configuration are not represented, so callers cannot accidentally publish them.
    """
    name: str
    description: str
    respond_to: str


@dataclass(frozen=True)
class DirectoryAnswer:
    apps: dict[str, str]  # agent pubkey -> its Feishu app id
    conflicts: int = 0  # app ids claimed by two agents, or colliding with one this host configures: used by nobody
    # Identities whose owner declares them another host's Feishu mirror ("feishu": {"mirror": true}, ADR-0020): not agents at
    # all — what they say is a Feishu group's words said once already, never relayed again.
    mirrors: frozenset[str] = frozenset()
    introductions: dict[str, AgentIntroduction] = field(default_factory=dict, compare=False)


def relay_query_url(relay_url: str) -> str:
    """The relay's POST /query, from the mirror env's BUZZ_RELAY_URL (the address the Buzz CLI uses for the same call)."""
    base = str(relay_url or "").rstrip("/")
    parts = urllib.parse.urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise GroupSyncError("BUZZ_RELAY_URL must be an http(s) address")
    return f"{base}/query"


def directory_filters(agents: Collection[str]) -> list[dict[str, Any]]:
    ordered = sorted(set(agents))
    return [{"kinds": [0], "authors": ordered}, {"kinds": [KIND_MANAGED_AGENT], "#d": ordered}]


def _newest(candidate: Mapping[str, Any], current: Mapping[str, Any] | None) -> bool:
    """NIP-01 replaceable order: the later created_at wins, a tie goes to the lowest id."""
    if current is None:
        return True
    if candidate["created_at"] != current["created_at"]:
        return candidate["created_at"] > current["created_at"]
    return candidate["id"] < current["id"]


def _event_shape_ok(event: Any) -> bool:
    return (isinstance(event, dict) and isinstance(event.get("id"), str) and isinstance(event.get("pubkey"), str)
            and isinstance(event.get("created_at"), int) and not isinstance(event.get("created_at"), bool)
            and isinstance(event.get("tags"), list) and isinstance(event.get("content"), str))


def _nip01_event_verified(event: Any) -> bool:
    """True only for a complete NIP-01 event whose canonical id and BIP-340 signature both verify.

    A relay normally checks these before storing an event, but its HTTP response is still untrusted input.  In particular,
    kind:30177 decides which Feishu application is invited into a group, so transport success must not become an implicit
    signature bypass.
    """
    if (not _event_shape_ok(event) or not isinstance(event.get("kind"), int) or isinstance(event.get("kind"), bool)
            or not HEX64_RE.fullmatch(event["id"]) or not HEX64_RE.fullmatch(event["pubkey"])
            or not isinstance(event.get("sig"), str) or re.fullmatch(r"[0-9a-f]{128}", event["sig"]) is None):
        return False
    try:
        serial = json.dumps([0, event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]],
                            separators=(",", ":"), ensure_ascii=False)
        event_id = hashlib.sha256(serial.encode()).hexdigest()
        return secrets.compare_digest(event_id, event["id"]) and sync.nk.schnorr_verify(
            bytes.fromhex(event["id"]), bytes.fromhex(event["pubkey"]), bytes.fromhex(event["sig"]))
    except (TypeError, ValueError, OverflowError):
        return False


def _first_tag(event: Mapping[str, Any], name: str) -> list[Any] | None:
    return next((t for t in event["tags"] if isinstance(t, list) and len(t) >= 2 and t[0] == name), None)


def _feishu_block(content: str) -> dict[str, Any]:
    try:
        body = json.loads(content)
    except ValueError:
        return {}
    feishu = body.get("feishu") if isinstance(body, dict) else None
    return feishu if isinstance(feishu, dict) else {}


def _claimed_app_id(content: str) -> str | None:
    app_id = _feishu_block(content).get("app_id")
    return app_id if isinstance(app_id, str) and APP_ID_RE.fullmatch(app_id) else None


def declares_mirror(content: str) -> bool:
    """The policy's owner says this identity is a Feishu group sync's mirror (ADR-0020): exactly `"mirror": true`."""
    return _feishu_block(content).get("mirror") is True


INTRO_NAME_MAX = 80
INTRO_DESCRIPTION_MAX = 1200
RESPOND_TO_MODES = frozenset({"anyone", "allowlist", "owner-only", "nobody"})


def _public_intro_text(value: Any, limit: int) -> str:
    """A short public profile field suitable for a chat message; control characters cannot shape the message."""
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 1, 0)].rstrip() + "…"


def _intro_from_events(profile_event: Mapping[str, Any], policy_event: Mapping[str, Any]) -> AgentIntroduction:
    try:
        profile = json.loads(profile_event["content"])
    except (KeyError, TypeError, ValueError):
        profile = {}
    try:
        policy = json.loads(policy_event["content"])
    except (KeyError, TypeError, ValueError):
        policy = {}
    profile = profile if isinstance(profile, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    name = _public_intro_text(profile.get("display_name") or profile.get("name") or policy.get("name"), INTRO_NAME_MAX)
    description = _public_intro_text(profile.get("about"), INTRO_DESCRIPTION_MAX)
    mode = policy.get("respond_to")
    return AgentIntroduction(name=name or "Agent", description=description,
                             respond_to=mode if isinstance(mode, str) and mode in RESPOND_TO_MODES else "")


def render_agent_introduction(intro: AgentIntroduction, *, relayed: bool = False) -> str:
    """Render public metadata in user language. Never accepts an instruction/prompt field."""
    if intro.respond_to == "anyone":
        reach = "我可以回应群里的任何成员。在群里 @我并说明要做什么即可。"
    elif intro.respond_to == "allowlist":
        reach = ("我目前只回应已授权成员。其他人 @我时会收到未响应的原因；如需使用，请联系 Agent 所有者申请授权，"
                 "或请已授权成员代为发起。")
    elif intro.respond_to == "owner-only":
        reach = ("我目前只回应我的所有者。其他人 @我时会收到未响应的原因；如需使用，请联系 Agent 所有者申请授权。")
    elif intro.respond_to == "nobody":
        reach = "我目前不接受群内 @，只执行已配置的自动任务。如需调整，请联系 Agent 所有者。"
    else:
        reach = "我的响应范围暂时无法确认。如需使用，请先联系 Agent 所有者。"
    prefix = "以下自我介绍由群助手根据 Agent 的公开资料代发：\n" if relayed else ""
    description = f"\n{intro.description}" if intro.description else ""
    return f"{prefix}大家好，我是 {intro.name}。{description}\n回应方式：{reach}"


def owner_signed_policies(events: Iterable[Any], agents: Collection[str]) -> tuple[dict[str, str], dict[str, Mapping[str, Any]]]:
    """({identity: owner}, {identity: its latest kind:30177}) for `agents`: the owner is the one the identity's latest profile
    declares (NIP-OA auth tag), and only a policy that owner signed counts — the rule Buzz Desktop applies. Malformed events are
    skipped, never fatal."""
    wanted = set(agents)
    profiles: dict[str, Mapping[str, Any]] = {}
    policies: list[Mapping[str, Any]] = []
    for event in events:
        if not _event_shape_ok(event):
            continue
        if event.get("kind") == 0 and event["pubkey"] in wanted and _newest(event, profiles.get(event["pubkey"])):
            profiles[event["pubkey"]] = event
        elif event.get("kind") == KIND_MANAGED_AGENT:
            policies.append(event)
    owners = {}
    for agent, prof in profiles.items():
        auth = _first_tag(prof, "auth")
        if auth is not None and isinstance(auth[1], str) and HEX64_RE.fullmatch(auth[1]):
            owners[agent] = auth[1]
    latest: dict[str, Mapping[str, Any]] = {}
    for event in policies:
        d = _first_tag(event, "d")
        agent = d[1] if d is not None else None
        if agent in owners and event["pubkey"] == owners[agent] and _newest(event, latest.get(agent)):
            latest[agent] = event
    return owners, latest


def parse_trusted_mirrors(events: Iterable[Any], candidates: Collection[str], roles: Mapping[str, str]) -> set[str]:
    """The `candidates` whose word about a Feishu person counts in this channel (ADR-0020): a bot member here, its owner-signed
    policy declares it a mirror, and that owner is an owner or admin of the channel (the one whose host runs the sync)."""
    owners, latest = owner_signed_policies(events, candidates)
    return {mirror for mirror, event in latest.items()
            if roles.get(mirror) == "bot" and declares_mirror(event["content"]) and roles.get(owners[mirror]) in ("owner", "admin")}


def parse_agent_directory(events: Iterable[Any], agents: Collection[str], reserved: Collection[str]) -> DirectoryAnswer:
    """{agent: app id} for the `agents` whose owner published one, and which of them are declared mirrors instead (never
    agents, whatever else their policy says). Malformed events are skipped, not fatal."""
    events = list(events)
    _, latest = owner_signed_policies(events, agents)
    profiles: dict[str, Mapping[str, Any]] = {}
    wanted = set(agents)
    for event in events:
        if (_event_shape_ok(event) and event.get("kind") == 0 and event["pubkey"] in wanted
                and _newest(event, profiles.get(event["pubkey"]))):
            profiles[event["pubkey"]] = event
    mirrors = frozenset(agent for agent, event in latest.items() if declares_mirror(event["content"]))
    claims = {agent: app for agent, event in latest.items()
              if agent not in mirrors and (app := _claimed_app_id(event["content"]))}
    counts: dict[str, int] = {}
    for app in claims.values():
        counts[app] = counts.get(app, 0) + 1
    bad = {app for app, n in counts.items() if n > 1 or app in reserved}
    introductions = {agent: _intro_from_events(profiles[agent], event) for agent, event in latest.items()
                     if agent not in mirrors and agent in profiles}
    return DirectoryAnswer(apps={agent: app for agent, app in claims.items() if app not in bad}, conflicts=len(bad),
                           mirrors=mirrors, introductions=introductions)


def fetch_agent_directory(cfg: Mapping[str, Any], relay_url: str, agents: Collection[str], reserved: Collection[str],
                          http: Any, now: datetime) -> DirectoryAnswer:
    """One signed POST /query (the people API's signer key: a member of the relay, never handed to a child) for the
    profiles and policies of `agents`; with more than PROFILE_QUERY_BATCH of them the profiles are read a few at a time (an
    answer must stay under the transport's cap). Any failure is a GroupSyncError that says nothing about the answer."""
    if len(set(agents)) <= PROFILE_QUERY_BATCH:
        return parse_agent_directory(_relay_query(cfg, relay_url, directory_filters(agents), http, now), agents, reserved)
    policies = _relay_query(cfg, relay_url, [{"kinds": [KIND_MANAGED_AGENT], "#d": sorted(set(agents))}], http, now)
    return parse_agent_directory([*policies, *_profiles(cfg, relay_url, agents, http, now)], agents, reserved)


# ---------------------------------------------------------------- two-way membership and reactions (ADR-0020)


# Config membership_sync: "two_way" (the default) compares the channel's members, the group's members and what both were at the
# end of the last round, and carries a change on either side to the other; "buzz_to_feishu" is the old one-way reconcile.
MEMBERSHIP_SYNC_MODES = ("two_way", "buzz_to_feishu")
DEFAULT_MEMBERSHIP_SYNC = "two_way"
# Config reaction_sync: "two_way" (the default) also carries people's reactions both ways; "agents_only" is the old behaviour
# (only an agent's Buzz reaction, from its own bot).
REACTION_SYNC_MODES = ("two_way", "agents_only")
DEFAULT_REACTION_SYNC = "two_way"
# Who a reaction or an approval the mirror carries into Buzz really came from: the Feishu person's Buzz pubkey. Only a
# message's reactions and approvals carry it: `buzz messages send` cannot add a tag to a message.
FEISHU_AUTHOR_TAG = "feishu-author"
FEISHU_SYNC_STATUS_TAG = "feishu-sync-status"
RELAY_WRITE_TIMEOUT = 15.0
# What relay-v0.2.1 answers when the added agent's channel_add_policy does not allow the adder (side_effects.rs).
POLICY_REFUSALS = ("policy:owner_only", "policy:nobody")
PEOPLE_KEEP = 5000  # people_seen: Feishu user key -> pubkey, the most recently seen kept
PEOPLE_CACHE_TTL = 30 * 86400  # a person another channel has not seen for this long drops out of the shared cache
PEOPLE_CACHE_MAX = 20000
MEMBER_NOTE_TTL = 30 * 86400
PROFILE_QUERY_BATCH = 5  # authors per kind 0 query: some profiles carry a ~190 KB inline avatar, an answer must stay < 1 MiB
REACTION_WATCH_SECONDS = 24 * 3600  # a message's Feishu reactions are read this long after it has a copy on both sides
JOIN_REQUEST_WATCH_SECONDS = 7 * 86400  # an agent's join request (ADR-0018) for as long as it can be answered
REACTION_WATCH_MAX = 200
REACTION_QUERY_BATCH = 20  # messages per `im reactions batch_query`
REACTION_PAGES_MAX = 3  # extra pages read for a message with more than 10 reactions; beyond that it is not judged this round
RELAY_CLOCK_SKEW_SECONDS = 900  # the relay takes a client created_at this far from its clock, no further
MEMBER_EVENTS_MAX = 256  # unknown outcomes are never evicted; reaching this cap applies fail-closed backpressure
MEMBER_EVENT_NONCE_TAG = "feishu-member-op"  # distinct logical operations in the same second must have distinct event ids
MEMBER_EVENT_STREAM_TAG = "feishu-member-stream"  # stable random producer id, independent of the current signer
MEMBER_EVENT_SEQUENCE_TAG = "feishu-member-seq"  # monotonic per stream, so random event ids are never used as order
MEMBER_EVENT_SEQUENCE_MAX = (1 << 63) - 1
MEMBER_EVENT_BLOCK_REASONS = frozenset({"retry_refused"})
JOIN_HEADER_RE = re.compile(r"^buzz-join:v1 JOIN-[0-9a-f]{8}\b", re.MULTILINE)
APPROVAL_COMMAND_RE = re.compile(r"/(approve|deny) (JOIN-[0-9a-f]{8})")


class RelayRefused(GroupSyncError):
    """The relay said no to an event: nothing was stored. `reason` is what it said (it can name a policy)."""

    def __init__(self, reason: str):
        super().__init__("the relay refused the event")
        self.reason = str(reason or "")[:300]


class MemberEventBlocked(GroupSyncError):
    """A pending member write cannot be retried without risking a second side effect."""

    def __init__(self, reason: str):
        super().__init__("a member event retry was blocked")
        self.reason = reason


def membership_sync_mode(cfg: Mapping[str, Any]) -> str:
    return cfg.get("membership_sync", DEFAULT_MEMBERSHIP_SYNC)


def reaction_sync_mode(cfg: Mapping[str, Any]) -> str:
    return cfg.get("reaction_sync", DEFAULT_REACTION_SYNC)


def refused_by_policy(exc: Exception) -> bool:
    """The added agent does not let this adder put it into a channel (its channel_add_policy): asking again changes nothing."""
    return isinstance(exc, RelayRefused) and exc.reason.startswith(POLICY_REFUSALS)


def relay_events_url(relay_url: str) -> str:
    return relay_query_url(relay_url)[: -len("/query")] + "/events"


def sign_event(secret_key_hex: str, kind: int, tags: list[list[str]], content: str, created_at: int) -> dict[str, Any]:
    """A NIP-01 event signed in this process (the key never reaches a child)."""
    pubkey = _signer_pubkey(secret_key_hex)
    serial = json.dumps([0, pubkey, created_at, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    event_id = hashlib.sha256(serial.encode()).hexdigest()
    sig = sync.nk.schnorr_sign(bytes.fromhex(event_id), bytes.fromhex(secret_key_hex), secrets.token_bytes(32)).hex()
    return {"id": event_id, "pubkey": pubkey, "created_at": created_at, "kind": kind, "tags": tags, "content": content,
            "sig": sig}


def publish_signed_event(relay_url: str, secret_key_hex: str, event: Mapping[str, Any], http: Any,
                         now: datetime, *, auth_tag: str | None = None) -> str:
    """POST an already signed event with a NIP-98 request from that same identity."""
    if not _nip01_event_verified(event) or event["pubkey"] != _signer_pubkey(secret_key_hex):
        raise GroupSyncError("the signed event does not belong to the request signer")
    url = relay_events_url(relay_url)
    body = json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode()
    headers = {"Authorization": nip98_header(secret_key_hex, "POST", url, now, body=body), "Content-Type": "application/json",
               "Accept": "application/json"}
    if auth_tag:
        headers["x-auth-tag"] = auth_tag
    try:
        status, answer = http(url, headers, RELAY_WRITE_TIMEOUT, body=body)
    except (OSError, GroupSyncError):
        raise GroupSyncError("the relay could not be reached to publish an event") from None
    try:
        doc = json.loads(answer)
    except ValueError:
        doc = None
    if status == 200 and isinstance(doc, dict):
        if doc.get("accepted") is True and doc.get("event_id") == event["id"]:
            return event["id"]
        if doc.get("accepted") is False:
            raise RelayRefused(str(doc.get("message") or ""))
    if 400 <= status < 500 and isinstance(doc, dict) and isinstance(doc.get("error"), str):
        raise RelayRefused(doc["error"])
    raise GroupSyncError(f"the relay answered an event with HTTP {status}")


def publish_event(relay_url: str, secret_key_hex: str, kind: int, tags: list[list[str]], content: str, http: Any,
                  now: datetime, *, auth_tag: str | None = None, created_at: int | None = None) -> str:
    """Sign one event and POST it to the relay's /events (the call `buzz` makes), the request signed (NIP-98) with the same key.
    An agent identity (the mirror) also sends its NIP-OA auth tag, or the relay does not take it for a member. Returns the event
    id once the relay accepted it (or already had this very event: "duplicate:"); RelayRefused when it said no; any other
    GroupSyncError is an unknown outcome. Callers that must retry the byte-for-byte event use `publish_signed_event`."""
    event = sign_event(secret_key_hex, kind, tags, content, int(now.timestamp()) if created_at is None else created_at)
    return publish_signed_event(relay_url, secret_key_hex, event, http, now, auth_tag=auth_tag)


def _relay_query(cfg: Mapping[str, Any], relay_url: str, filters: list[dict[str, Any]], http: Any, now: datetime) -> list[Any]:
    """One signed POST /query with the people API's signer key (a member of the relay, never handed to a child)."""
    key = load_signer_key(Path(cfg["people_api"]["signer_env_file"]))
    url = relay_query_url(relay_url)
    body = json.dumps(filters, separators=(",", ":")).encode()
    headers = {"Authorization": nip98_header(key, "POST", url, now, body=body), "Content-Type": "application/json",
               "Accept": "application/json"}
    try:
        status, answer = http(url, headers, DIRECTORY_TIMEOUT, body=body)
    except (OSError, GroupSyncError):
        raise GroupSyncError("the relay could not be asked for the agent directory") from None
    if status != 200:
        raise GroupSyncError(f"the relay answered the agent directory query with HTTP {status}")
    try:
        events = json.loads(answer)
    except ValueError:
        raise GroupSyncError("the relay's agent directory answer is not JSON") from None
    if not isinstance(events, list):
        raise GroupSyncError("the relay's agent directory answer is not a list of events")
    return [event for event in events if _nip01_event_verified(event)]


def _profiles(cfg: Mapping[str, Any], relay_url: str, authors: Collection[str], http: Any, now: datetime) -> list[Any]:
    """The kind 0 of `authors`, a few per query: a profile can carry an inline avatar of ~190 KB (seen live), and one answer
    must stay under the transport's cap."""
    events: list[Any] = []
    for group in batches(sorted(set(authors)), PROFILE_QUERY_BATCH):
        events += _relay_query(cfg, relay_url, [{"kinds": [0], "authors": group}], http, now)
    return events


def fetch_agent_index(cfg: Mapping[str, Any], relay_url: str, reserved: Collection[str], http: Any, now: datetime, *,
                      wanted: Collection[str]) -> dict[str, str]:
    """{Feishu app id: agent pubkey} for the `wanted` app ids that an agent's owner published: every kind:30177 on the relay
    (small), then only the profiles of the agents whose policy claims one of them, checked the same way as the directory
    (ADR-0019). Read only in a round in which a bot nobody here knows has joined the group."""
    wanted = set(wanted)
    policies = [e for e in _relay_query(cfg, relay_url, [{"kinds": [KIND_MANAGED_AGENT]}], http, now)
                if _event_shape_ok(e) and e.get("kind") == KIND_MANAGED_AGENT]
    claimants = {d[1] for e in policies if (d := _first_tag(e, "d")) is not None and isinstance(d[1], str)
                 and HEX64_RE.fullmatch(d[1]) and _claimed_app_id(e["content"]) in wanted}
    if not claimants:
        return {}
    answer = parse_agent_directory([*policies, *_profiles(cfg, relay_url, claimants, http, now)], claimants, reserved)
    return {app: agent for agent, app in answer.apps.items() if app in wanted}


def _check_cache_entry(key: Any, entry: Any) -> bool:
    return (isinstance(key, str) and isinstance(entry, dict) and isinstance(entry.get("pubkey"), str)
            and bool(HEX64_RE.fullmatch(entry["pubkey"])) and isinstance(entry.get("seen"), int)
            and not isinstance(entry.get("seen"), bool))


def merge_people_cache(path: Path, fresh: Mapping[str, str], now: int) -> dict[str, str]:
    """The host-wide people cache (config people_cache_file, shared by every group sync on this host): this channel's people are
    written in, everybody seen by any channel in the last PEOPLE_CACHE_TTL comes back as {Feishu user key: pubkey}. A 0600
    regular file, locked while it is rewritten in place; an unreadable content is started over (it is only a cache)."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as fh:
        meta = os.fstat(fh.fileno())
        if not stat.S_ISREG(meta.st_mode) or not _owner_only(meta):
            raise GroupSyncError("people_cache_file must be an owner-only (0600) regular file")
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            doc = json.loads(fh.read() or "{}")
        except ValueError:
            doc = {}
        people = doc.get("people") if isinstance(doc, dict) and isinstance(doc.get("people"), dict) else {}
        people = {k: v for k, v in people.items() if _check_cache_entry(k, v) and now - v["seen"] <= PEOPLE_CACHE_TTL}
        for key, pubkey in fresh.items():
            people.pop(key, None)
            people[key] = {"pubkey": pubkey, "seen": now}
        people = dict(list(people.items())[-PEOPLE_CACHE_MAX:])
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"version": 1, "people": people}, ensure_ascii=False, separators=(",", ":")))
    return {key: entry["pubkey"] for key, entry in people.items()}


@dataclass(frozen=True)
class MemberListing:
    users: dict[str, str]  # member id (in the chosen identity space) -> Feishu display name
    bots: dict[str, str]  # app id -> the bot's member id
    complete: bool  # every page was read: only then can somebody be missing from it for a reason


@dataclass
class MemberDelta:
    """What the two-way comparison did to the channel this round, and what that means for the group."""
    adopted: list[str] = field(default_factory=list)  # pubkeys added to the channel because they joined the group
    dropped: list[str] = field(default_factory=list)  # pubkeys removed from the channel because they left the group
    keep_users: set[str] = field(default_factory=set)  # group members (ids) that are channel members now
    keep_bots: set[str] = field(default_factory=set)  # app ids of bots whose agent is a channel member now
    drop_users: set[str] = field(default_factory=set)  # ids of people who left both
    drop_bots: set[str] = field(default_factory=set)
    remove_users: set[str] = field(default_factory=set)  # left the channel: out of the group too (ids)
    remove_bots: set[str] = field(default_factory=set)
    # Gone from the group (or maybe gone: the list was not read whole) while the channel removal waits — withheld, failed —
    # or cannot be judged yet: not put back into the group, so the next round still sees them gone.
    hold_users: set[str] = field(default_factory=set)
    hold_bots: set[str] = field(default_factory=set)
    leaving: dict[str, str] = field(default_factory=dict)  # pubkey -> Feishu key of members taken out of the group for leaving
    keys: dict[str, str] = field(default_factory=dict)  # adopted pubkey -> its Feishu key


def _key_member_id(key: str) -> str:
    """The id part of a Feishu key: "u:<id>", "o:<app>:<id>" or "b:<app id>"."""
    return key.rsplit(":", 1)[-1]


def _member_source_key_ok(key: str) -> bool:
    """A persisted Feishu member key: bot app id, union id, or owner-app/open-id pair."""
    if key.startswith("b:"):
        return APP_ID_RE.fullmatch(key[2:]) is not None
    if key.startswith("u:"):
        return UNION_ID_RE.fullmatch(key[2:]) is not None
    if key.startswith("o:"):
        parts = key.split(":", 2)
        return (len(parts) == 3 and APP_ID_RE.fullmatch(parts[1]) is not None
                and OPEN_ID_RE.fullmatch(parts[2]) is not None)
    return False


def _member_event_key(kind: int, pubkey: str, role: str | None, source_key: str) -> str:
    return f"{kind}|{pubkey}|{role or ''}|{source_key}"


def _new_member_event_nonce() -> str:
    return secrets.token_hex(32)


def _new_member_event_stream() -> str:
    return secrets.token_hex(32)


def member_event_stream(event: Any) -> str | None:
    """The canonical group-sync stream carried by a signed membership event, if any."""
    if not isinstance(event, dict):
        return None
    tags = sync._tag_values(event, MEMBER_EVENT_STREAM_TAG)
    if len(tags) != 1 or len(tags[0]) != 2 or not isinstance(tags[0][1], str):
        return None
    value = tags[0][1]
    return value if HEX64_RE.fullmatch(value) is not None else None


def member_event_sequence(event: Any) -> int | None:
    """The canonical group-sync operation sequence carried by a signed membership event, if any."""
    if not isinstance(event, dict):
        return None
    tags = sync._tag_values(event, MEMBER_EVENT_SEQUENCE_TAG)
    if len(tags) != 1 or len(tags[0]) != 2 or not isinstance(tags[0][1], str):
        return None
    value = tags[0][1]
    if not re.fullmatch(r"[1-9][0-9]{0,18}", value):
        return None
    sequence = int(value)
    return sequence if sequence <= MEMBER_EVENT_SEQUENCE_MAX else None


def member_event_position(event: Any) -> tuple[str, int] | None:
    """A membership event's comparable persistent position, independent of its signer and client clock."""
    stream, sequence = member_event_stream(event), member_event_sequence(event)
    return (stream, sequence) if stream is not None and sequence is not None else None


def _parse_member_event_key(value: str) -> tuple[int, str, str | None, str] | None:
    parts = value.split("|", 3)
    if len(parts) != 4 or parts[0] not in ("9000", "9001") or HEX64_RE.fullmatch(parts[1]) is None:
        return None
    kind, pubkey, role, source_key = int(parts[0]), parts[1], parts[2] or None, parts[3]
    if not _member_source_key_ok(source_key):
        return None
    expected_role = "bot" if source_key.startswith("b:") else "member"
    if (kind == 9000 and role != expected_role) or (kind == 9001 and role is not None):
        return None
    return kind, pubkey, role, source_key


def _member_event_record_ok(operation: str, event: Any, binding: str) -> bool:
    """A pending operation must contain exactly the event that can safely be retried."""
    parsed = _parse_member_event_key(operation)
    channel, separator, _ = binding.partition("|")
    if (parsed is None or not separator or UUID_RE.fullmatch(channel) is None or not _nip01_event_verified(event)
            or set(event) != {"id", "pubkey", "created_at", "kind", "tags", "content", "sig"}):
        return False
    kind, target, role, _ = parsed
    tags = [["h", channel], ["p", target]] + ([["role", role]] if role else [])
    if not (isinstance(event["created_at"], int) and not isinstance(event["created_at"], bool)
            and event["created_at"] > 0 and event["kind"] == kind and event["content"] == ""
            and isinstance(event["tags"], list) and len(event["tags"]) == len(tags) + 3
            and event["tags"][:-3] == tags):
        return False
    nonce, stream, sequence = event["tags"][-3:]
    return (isinstance(nonce, list) and len(nonce) == 2 and nonce[0] == MEMBER_EVENT_NONCE_TAG
            and isinstance(nonce[1], str) and HEX64_RE.fullmatch(nonce[1]) is not None
            and isinstance(stream, list) and len(stream) == 2 and stream[0] == MEMBER_EVENT_STREAM_TAG
            and member_event_stream(event) is not None
            and isinstance(sequence, list) and len(sequence) == 2 and sequence[0] == MEMBER_EVENT_SEQUENCE_TAG
            and member_event_sequence(event) is not None)


# ---------------------------------------------------------------- preflight and membership


def preflight_new_chat(*, owner_in_scope: bool, bot_scopes: set[str], profile_ok: bool) -> Preflight:
    problems = []
    if not profile_ok:
        problems.append("owner_profile_mismatch")
    if not owner_in_scope:
        problems.append("owner_out_of_app_scope")
    if "im:chat:create" not in bot_scopes:
        problems.append("bot_missing_im:chat:create")
    return Preflight(ok=not problems, problems=tuple(problems), can_remove=True)


def preflight_existing_chat(chat: Mapping[str, Any], *, owner_open_id: str, bots_to_add: int,
                            remove_extras: bool) -> Preflight:
    problems: list[str] = []
    warnings: list[str] = []
    if chat.get("external") is not False:
        problems.append("external_chat")
    if chat.get("chat_status") != "normal":
        problems.append("chat_not_normal")
    if chat.get("chat_mode") == "topic":
        problems.append("topic_mode_unsupported")
    elif chat.get("chat_mode") != "group":
        problems.append("chat_mode_unsupported")
    privileged = owner_open_id == chat.get("owner_id") or owner_open_id in (chat.get("user_manager_id_list") or [])
    if chat.get("add_member_permission") != "all_members" and not privileged:
        problems.append("cannot_add_members")
    if remove_extras and not privileged:
        problems.append("cannot_remove_members")
    if not remove_extras:
        warnings.append("extras_stay_and_see_channel_messages")
    if chat.get("moderation_permission", "all_members") != "all_members":
        warnings.append("moderation_restricted")
    if chat.get("share_card_permission") == "allowed":
        warnings.append("share_card_allowed")
    if chat.get("membership_approval") == "no_approval_required":
        warnings.append("join_without_approval")
    if int(chat.get("bot_count") or 0) + max(bots_to_add, 0) > MAX_BOTS_PER_CHAT:
        problems.append("bot_limit")
    return Preflight(ok=not problems, problems=tuple(problems), warnings=tuple(warnings), can_remove=privileged)


def plan_membership(*, desired_users: set[str], desired_bots: set[str], actual_users: set[str],
                    actual_bots: set[str], owner_open_id: str, owner_app_id: str, managed_bots: set[str],
                    remove_extras: bool, protected: frozenset[str] = frozenset()) -> MembershipPlan:
    """Users are ids in the chosen identity space (`owner_open_id` is the owner's id there, whatever its kind);
    `protected` are users that stay whoever else is wanted (the group's own owner)."""
    users_want = set(desired_users) | {owner_open_id} | set(protected)
    foreign = set(actual_bots) - set(managed_bots) - {owner_app_id}
    capacity = max(MAX_BOTS_PER_CHAT - len(foreign) - 1, 0)
    wanted = set(desired_bots) - {owner_app_id}
    # Bots already in the group keep their seat; newcomers queue by app id.
    order = sorted(wanted & set(actual_bots)) + sorted(wanted - set(actual_bots))
    kept, blocked = order[:capacity], sorted(order[capacity:])
    bots_want = set(kept) | {owner_app_id}
    remove_users: list[str] = []
    remove_bots: list[str] = []
    if remove_extras:
        remove_users = sorted(set(actual_users) - users_want)
        remove_bots = sorted((set(actual_bots) & set(managed_bots)) - bots_want)
    return MembershipPlan(
        add_users=tuple(sorted(users_want - set(actual_users))),
        remove_users=tuple(remove_users),
        add_bots=tuple(sorted(bots_want - set(actual_bots))),
        remove_bots=tuple(remove_bots),
        blocked_bots=tuple(blocked),
    )


def guard_removals(plan: MembershipPlan, *, unmapped: int, allow_bulk: bool) -> tuple[MembershipPlan, str | None]:
    """An unmapped channel member cannot be told apart from an outsider, and a
    bad people export makes everyone look unmapped: withhold removals then."""
    reason = None
    if plan.remove_users and unmapped:
        plan, reason = replace(plan, remove_users=()), "unmapped_members"
    if len(plan.remove_users) + len(plan.remove_bots) > BULK_REMOVAL_LIMIT and not allow_bulk:
        plan, reason = replace(plan, remove_users=(), remove_bots=()), "bulk_removal"
    return plan, reason


def batches(items: list[Any], size: int) -> list[list[Any]]:
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


# ---------------------------------------------------------------- text


def _single_line(value: str) -> str:
    return " ".join(value.split())  # also splits on U+2028, U+2029, U+0085, VT and FF


def _safe_name(name: str) -> str:
    """Display names are user-controlled: no format characters, no line breaks,
    no markup, and no @ or nostr: the Buzz CLI would resolve into a mention."""
    visible = "".join(ch for ch in name if unicodedata.category(ch) != "Cf")
    return sync.neutralize(_single_line(visible)).translate(NAME_TRANSLATION).strip()


def _signature_probe(line: str) -> str:
    """What a reader sees: NFKC, without format characters, combining marks, variation
    selectors or blank-looking fillers."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", line)
                   if unicodedata.category(ch) not in {"Cf", "Mn", "Me", "Cc"} and ch not in INVISIBLE_FILLERS)


def _context_signature_probe(line: str) -> str:
    """What a reader of a markdown client sees at the start of a line: _signature_probe without the characters nobody can see
    (unassigned and private-use code points too), without the backslashes that escape a bracket and without the bold / quote /
    code / list / heading marks and bullets in front of it."""
    seen = "".join(ch for ch in _signature_probe(line) if unicodedata.category(ch) not in {"Cn", "Co"})
    return CONTEXT_LINE_DRESSING_RE.sub("", seen.replace("\\", ""))


def _mark_forged_lines(body: str, signature: re.Pattern[str], probe: Callable[[str], str] = _signature_probe) -> str:
    parts = LINE_BREAK_RE.split(BIDI_CONTROLS_RE.sub("", body))  # text, break, text, break, ...
    for i in range(2, len(parts), 2):
        if signature.search(probe(parts[i])):
            parts[i] = CONTINUATION_MARK + parts[i]
    return "".join(parts)


def _buzz_parent(event: Mapping[str, Any]) -> str | None:
    root = None
    for tag in event.get("tags") or []:
        if isinstance(tag, list) and len(tag) >= 4 and tag[0] == "e" and isinstance(tag[1], str) and HEX64_RE.fullmatch(tag[1]):
            if tag[3] == "reply":
                return tag[1]
            if tag[3] == "root":
                root = tag[1]
    return root


def _buzz_root(event: Mapping[str, Any]) -> str | None:
    """The thread root a reply names itself (a nested reply carries root and reply markers; a direct reply only reply)."""
    root = None
    for tag in event.get("tags") or []:
        if isinstance(tag, list) and len(tag) >= 4 and tag[0] == "e" and isinstance(tag[1], str) and HEX64_RE.fullmatch(tag[1]) \
                and tag[3] == "root":
            root = tag[1]
    return root


# ---------------------------------------------------------------- images


def sniff_image(data: bytes) -> str | None:
    """The image format the bytes are, by magic number only (never the declared type or the file name)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if data[:2] == b"BM" and len(data) >= 18 and struct.unpack("<I", data[14:18])[0] in BMP_DIB_HEADER_SIZES:
        return "bmp"
    return None


def _image_ref(fields: Mapping[str, str]) -> ImageRef | str:
    match = MEDIA_URL_RE.fullmatch(fields.get("url", ""))
    if not match:
        return "bad_imeta"
    sha, ext = match.group(1), match.group(2) or ""
    if fields.get("x", sha) != sha:  # the event's own hash must agree with the address it names
        return "bad_imeta"
    size = int(fields["size"]) if re.fullmatch(r"[0-9]{1,12}", fields.get("size", "")) else None
    if size is not None and size > IMAGE_MAX_BYTES:
        return "too_large"
    return ImageRef(sha + ext, sha, size)


def event_images(event: Mapping[str, Any]) -> tuple[tuple[ImageRef | str, ...], int, frozenset[str]]:
    """(attachments in imeta order, how many are beyond IMAGES_PER_EVENT, every imeta url of the event). An
    attachment that cannot be fetched is kept as its skip reason, so it is counted once like any other."""
    entries: list[ImageRef | str] = []
    urls: set[str] = set()
    seen: set[str] = set()
    for tag in event.get("tags") or []:
        if not (isinstance(tag, list) and tag and tag[0] == "imeta"):
            continue
        fields: dict[str, str] = {}
        for item in tag[1:]:
            key, sep, value = item.partition(" ") if isinstance(item, str) else ("", "", "")
            if sep and key not in fields:
                fields[key] = value
        if "url" in fields:
            urls.add(fields["url"])
        entry = _image_ref(fields)
        if isinstance(entry, ImageRef):
            if entry.sha256 in seen:
                continue
            seen.add(entry.sha256)
        entries.append(entry)
    return tuple(entries[:IMAGES_PER_EVENT]), max(len(entries) - IMAGES_PER_EVENT, 0), frozenset(urls)


def read_image(path: Path, *, allowed: frozenset[str]) -> tuple[bytes, str]:
    """The bytes and format of a downloaded file, judged by content; ImageSkip when it is not an allowed image. A
    symlink or anything but a regular file is refused, and no more than the limit (plus one byte) is ever read."""
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode):
            raise ImageSkip("unreadable")
        if meta.st_size > IMAGE_MAX_BYTES:
            raise ImageSkip("too_large")
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            data = fh.read(IMAGE_MAX_BYTES + 1)
    except OSError:
        raise ImageSkip("unreadable") from None
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > IMAGE_MAX_BYTES:
        raise ImageSkip("too_large")
    kind = sniff_image(data)
    if kind is None:
        raise ImageSkip("not_image")
    if kind not in allowed:
        raise ImageSkip("unsupported_format")
    return data, kind


def _strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("not a JPEG")
    out = bytearray(b"\xff\xd8")
    n, i = len(data), 2
    while True:
        if i + 2 > n or data[i] != 0xFF:
            raise ValueError("bad JPEG marker")
        if data[i + 1] == 0xFF:  # fill byte
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xD9:  # EOI: whatever follows is not the picture
            out += b"\xff\xd9"
            return bytes(out)
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # markers without a length
            out += data[i:i + 2]
            i += 2
            continue
        if i + 4 > n:
            raise ValueError("truncated JPEG")
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if length < 2 or i + 2 + length > n:
            raise ValueError("JPEG segment out of bounds")
        segment, i = data[i:i + 2 + length], i + 2 + length
        if marker == 0xFE or 0xE1 <= marker <= 0xEF:  # comment, EXIF / ICC / IPTC / XMP ... (APP0 = JFIF stays)
            continue
        out += segment
        if marker == 0xDA:  # entropy-coded data up to the next real marker (FF00 is a stuffed byte, FFD0-D7 a restart)
            j = i
            while True:
                k = data.find(b"\xff", j)
                if k < 0 or k + 1 >= n:
                    raise ValueError("JPEG has no end of image")
                following = data[k + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7:
                    j = k + 2
                elif following == 0xFF:
                    j = k + 1
                else:
                    break
            out += data[i:k]
            i = k


def _strip_png(data: bytes) -> bytes:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG")
    out = bytearray(PNG_SIGNATURE)
    n, i, first = len(data), len(PNG_SIGNATURE), True
    while i + 12 <= n:
        size, kind = struct.unpack(">I", data[i:i + 4])[0], data[i + 4:i + 8]
        end = i + 12 + size
        if end > n or (first and kind != b"IHDR"):
            raise ValueError("bad PNG chunk")
        first = False
        if kind in PNG_DRAWING_CHUNKS:
            out += data[i:end]  # verbatim, checksum included
        i = end
        if kind == b"IEND":  # whatever follows is not the picture
            return bytes(out)
    raise ValueError("PNG has no IEND")


def _strip_webp(data: bytes) -> bytes:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("not a WebP")
    riff = struct.unpack("<I", data[4:8])[0]
    if riff < 4 or riff + 8 > len(data):
        raise ValueError("bad WebP size")
    limit, i, chunks, has_image = riff + 8, 12, [], False
    while i < limit:
        if i + 8 > limit:
            raise ValueError("truncated WebP chunk")
        fourcc, size = data[i:i + 4], struct.unpack("<I", data[i + 4:i + 8])[0]
        end = i + 8 + size + (size & 1)
        if end > limit:
            raise ValueError("WebP chunk out of bounds")
        if fourcc in WEBP_KEPT_CHUNKS:
            payload = data[i + 8:i + 8 + size]
            if fourcc == b"VP8X":
                if size < 10:
                    raise ValueError("bad VP8X")
                payload = bytes([payload[0] & ~WEBP_METADATA_FLAGS & 0xFF]) + payload[1:]  # no longer announces what is gone
            has_image = has_image or fourcc in (b"VP8 ", b"VP8L", b"ANMF")
            chunks.append(fourcc + struct.pack("<I", size) + payload + (b"\x00" if size & 1 else b""))
        i = end
    if not has_image:
        raise ValueError("WebP has no image data")
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _strip_gif(data: bytes) -> bytes:
    n = len(data)
    if data[:6] not in (b"GIF87a", b"GIF89a") or n < 13:
        raise ValueError("not a GIF")
    i = 13 + (3 * (2 << (data[10] & 7)) if data[10] & 0x80 else 0)
    if i > n:
        raise ValueError("truncated GIF")
    out = bytearray(data[:i])

    def blocks_end(j: int) -> int:  # the end of a run of length-prefixed sub-blocks (a zero length ends it)
        while True:
            if j >= n:
                raise ValueError("truncated GIF block")
            size = data[j]
            j += 1 + size
            if size == 0:
                return j
            if j > n:
                raise ValueError("truncated GIF block")

    while i < n:
        block = data[i]
        if block == 0x3B:  # trailer: whatever follows is not the picture
            return bytes(out) + b"\x3b"
        if block == 0x21:
            if i + 2 > n:
                raise ValueError("truncated GIF extension")
            label, end = data[i + 1], blocks_end(i + 2)
            keep = label == 0xF9  # graphic control (frame delay / transparency)
            if label == 0xFF:  # application extension: only the animation loop is kept
                keep = data[i + 3:i + 3 + data[i + 2]] in GIF_KEPT_APPLICATIONS
            if keep:
                out += data[i:end]
            i = end
        elif block == 0x2C:
            if i + 10 > n:
                raise ValueError("truncated GIF frame")
            j = i + 10 + (3 * (2 << (data[i + 9] & 7)) if data[i + 9] & 0x80 else 0) + 1
            if j > n:
                raise ValueError("truncated GIF frame")
            end = blocks_end(j)
            out += data[i:end]
            i = end
        else:
            raise ValueError("bad GIF block")
    raise ValueError("GIF has no trailer")


def strip_metadata(data: bytes, kind: str) -> bytes:
    """`data` without the metadata Buzz's relay refuses (EXIF, ICC profile, XMP, comments, trailing bytes ...), the picture itself
    untouched and never re-encoded. ValueError when the file is not well-formed or the format cannot be cleaned."""
    cleaner = {"jpeg": _strip_jpeg, "png": _strip_png, "webp": _strip_webp, "gif": _strip_gif}.get(kind)
    if cleaner is None:
        raise ValueError(f"metadata cannot be stripped from {kind or 'unknown'} images")
    try:
        return cleaner(data)
    except (IndexError, struct.error):
        raise ValueError("malformed image") from None


def _markdown_without_attachments(content: str, attachment_urls: frozenset[str]) -> str:
    """Markdown image syntax of an attachment is dropped (the image is sent as an image); any other `![alt](url)` is never
    fetched and becomes "[图片：alt] url" (no url unless http/https), so a raw markdown string never reaches Feishu."""
    dropped = False

    def replace(match: re.Match[str]) -> str:
        nonlocal dropped
        alt, url = match.group(1).strip(), match.group(2)
        if url in attachment_urls:
            dropped = True
            return ""
        label = f"[图片：{alt}]" if alt.lower() not in ("", "image") else "[图片]"  # "image" is the CLI's own placeholder alt
        return f"{label} {url}" if re.match(r"https?://", url, re.IGNORECASE) else label

    text = MARKDOWN_IMAGE_RE.sub(replace, content)
    return re.sub(r"\n{3,}", "\n\n", text).strip() if dropped else text


# ---------------------------------------------------------------- cards


@dataclass(frozen=True)
class CardMention:
    """Someone a Buzz event mentions (a p tag), as a card can name them: by address, by an id of the sending app, or
    by a name that notifies nobody."""
    name: str
    email: str | None = None
    open_id: str | None = None


def buzz_thread_root(event: Mapping[str, Any]) -> str | None:
    """The NIP-10 root of the thread an event is in, read as the bridge reads it (relaydb.threadRoot): the e tag marked
    "root"; with no marker on any e tag, the first one; None outside a thread, when the markers name no root (only
    "reply" or "mention"), or when the id is not 64 lower-case hex."""
    first: str | None = None
    marked = False
    tags = event.get("tags")
    for tag in tags if isinstance(tags, list) else []:
        if not (isinstance(tag, list) and len(tag) >= 2 and tag[0] == "e" and isinstance(tag[1], str)):
            continue
        marker = tag[3] if len(tag) >= 4 and isinstance(tag[3], str) else ""
        if marker == "root":
            return tag[1] if HEX64_RE.fullmatch(tag[1]) else None
        if marker:
            marked = True
        elif first is None:
            first = tag[1]
    return None if marked or first is None or not HEX64_RE.fullmatch(first) else first


def open_link(base_url: str, event_id: str, channel_id: str, thread_root: str | None) -> str | None:
    """The https page that opens a message in Buzz Desktop (the bridge's /bind/open): ids only. The page's own
    signature (s, n) only protects the channel name it may show, so the link carries neither and needs no key. Feishu's
    desktop client drops custom schemes such as buzz://, so this is the only kind of link a card gets. None when the
    base is not a bare https origin or an id is not 64 lower-case hex / a lower-case uuid."""
    parts = urllib.parse.urlsplit(base_url) if isinstance(base_url, str) else None
    if (parts is None or parts.scheme != "https" or not parts.netloc or parts.path or parts.query or parts.fragment
            or base_url != base_url.strip() or " " in base_url):
        return None
    if not (HEX64_RE.fullmatch(event_id) and UUID_RE.fullmatch(channel_id)):
        return None
    if thread_root is not None and not HEX64_RE.fullmatch(thread_root):
        return None
    query = f"e={event_id}&c={channel_id}" + (f"&t={thread_root}" if thread_root else "")
    return f"{base_url}{CARD_OPEN_PATH}?{query}"


def card_markdown(content: str) -> str:
    """User text as it goes into a card's markdown. Markdown itself is kept (a Buzz message is markdown), but nothing
    that makes a card tag survives: every `<` becomes a full-width one, so no <at id=all>, <font>, <a> or forged @ can
    be written. Bidirectional controls go, lines that look like the other side's signature get the usual mark, every
    kind of line break becomes \n, other control characters and lone surrogates go, and a buzz:// link (Feishu drops
    custom schemes) is defused."""
    text = content.encode("utf-8", "replace").decode("utf-8")
    text = _mark_forged_lines(text.replace("<", "＜"), FORGED_FEISHU_SIGNATURE_RE)
    text = CARD_CONTROL_RE.sub("", LINE_BREAK_RE.sub("\n", text))
    return BUZZ_SCHEME_RE.sub("buzz：//", text).strip()


def _card_readable(text: str) -> str:
    """The (already neutralised) text of a message as a reader should see it: a GitLab -> Buzz sync message without the lines that
    are there for programs (skills#134). The machine header, a whole line that starts with `[gitlab-notify:v1]`, is looked for
    only where the sync itself reads it: the last line (messages since 2026-09-18) and the first (older ones); the same text
    quoted in the middle of a message or of a sentence is what its author wrote and stays. In a message that has a header the
    `🔔 通知 @…` line goes too (ADR-0012: it makes the Buzz client highlight the @, and the card has an @ line of its own), and
    in a legacy `key: value` message (header first, `title: …` second) the title line loses its key: from here on it is the
    message's first line. Any other text comes back unchanged, so a message that only looks like a sync message is left alone."""
    lines = text.split("\n")
    first, last = lines[0].startswith(CARD_NOTIFY_HEADER), lines[-1].startswith(CARD_NOTIFY_HEADER)
    if not (first or last):
        return text
    if first and len(lines) > 1:
        titled = CARD_LEGACY_TITLE_RE.fullmatch(lines[1])
        if titled:
            lines[1] = titled.group(1)
    kept = [line for index, line in enumerate(lines)
            if not (index == 0 and first or index == len(lines) - 1 and last) and not line.startswith(CARD_NOTIFIED_PREFIX)]
    while kept and not kept[0].strip():
        kept.pop(0)
    return "\n".join(kept).rstrip()


def _card_status_history(source: str, readable: str) -> tuple[str, str | None]:
    """Split a GitLab sync's trailing human-readable status history from the text that stays in the folded panel.

    The producer owns this small display contract: the exact ``状态记录`` heading and one or more ``- `` records must be
    the final readable block, immediately before the sync's first/last machine header.  A similarly named block in an
    ordinary message or in the middle of a sync remains ordinary folded content.  Keeping the recognition this narrow
    prevents arbitrary prose from unexpectedly changing the card layout.
    """
    source_lines = source.split("\n")
    if not (source_lines[0].startswith(CARD_NOTIFY_HEADER) or source_lines[-1].startswith(CARD_NOTIFY_HEADER)):
        return readable, None
    lines = readable.split("\n")
    try:
        start = max(index for index, line in enumerate(lines) if line.strip() == CARD_STATUS_HISTORY_TITLE)
    except ValueError:
        return readable, None
    records = lines[start + 1:]
    if start == 0 or not records or any(not line.startswith("- ") for line in records):
        return readable, None
    main = "\n".join(lines[:start]).rstrip()
    if not main:
        return readable, None
    return main, "\n".join(lines[start:])


def _card_gitlab_url(source: str, readable: str) -> str | None:
    """The deterministic GitLab object URL of a real sync message, never an arbitrary message link.

    A sync machine header must be at the same trusted edge `_card_readable` recognises.  Current messages wrap their
    generated headline in a markdown link; legacy/V2 messages may instead carry a labelled or standalone GitLab object
    URL.  Every accepted target is a credential-free HTTPS URL with GitLab's ``/-/`` object path.
    """
    source_lines = source.split("\n")
    if not (source_lines[0].startswith(CARD_NOTIFY_HEADER) or source_lines[-1].startswith(CARD_NOTIFY_HEADER)):
        return None

    def accepted(value: str) -> str | None:
        parts = urllib.parse.urlsplit(value)
        if (parts.scheme != "https" or not parts.netloc or parts.username is not None or parts.password is not None
                or "/-/" not in parts.path or any(char.isspace() for char in value)):
            return None
        return value

    lines = readable.split("\n")
    first = next((line for line in lines if line.strip()), "")
    headline = re.search(r"\]\((https://[^)\s]+)\)", first)
    if headline:
        return accepted(headline.group(1))
    for line in lines:
        labelled = re.fullmatch(r"(?:url|MR|Issue|Pipeline|Comment|GitLab)[：:]\s*(https://\S+)", line.strip(), re.IGNORECASE)
        if labelled and (url := accepted(labelled.group(1))):
            return url
        if re.fullmatch(r"https://\S+", line.strip()) and (url := accepted(line.strip())):
            return url
    return None


def _card_without_gitlab_url_line(readable: str, target: str | None) -> str:
    """Move a standalone/labelled GitLab target from readable body text to its button without duplicating it.

    A generated headline markdown link stays: it carries the human title as well as the target.  Only a line whose whole
    purpose is the exact selected URL is removed; the Buzz event and text-mode fallback remain untouched.
    """
    if target is None:
        return readable
    kept: list[str] = []
    removed = False
    for line in readable.split("\n"):
        stripped = line.strip()
        labelled = re.fullmatch(r"(?:url|MR|Issue|Pipeline|Comment|GitLab)[：:]\s*(https://\S+)", stripped, re.IGNORECASE)
        if not removed and ((labelled and labelled.group(1) == target) or stripped == target):
            removed = True
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).rstrip()


def _card_name(name: str) -> str:
    """A name, channel name or label of a card: one clean line, never long."""
    return BUZZ_SCHEME_RE.sub("buzz：//", _safe_name(name))[:CARD_NAME_CHARS].strip()


def _fit_columns(text: str, columns: int) -> str:
    """`text` as it is when it is at most `columns` display columns wide (a wide character counts 2, any other 1), else its
    longest prefix that leaves two columns for an ellipsis, plus the ellipsis (a wide one on a phone)."""
    def width(ch: str) -> int:
        return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    if sum(map(width, text)) <= columns:
        return text
    used, end = 0, 0
    for end, ch in enumerate(text):
        used += width(ch)
        if used > columns - 2:
            break
    return text[:end].rstrip() + "…"


def _plain_markdown(line: str) -> tuple[str, int]:
    """One line of markdown as plain text, the one cleaning of a card's title and summary: a link or image keeps its text (the
    second value counts them: their address is gone), bold, strike-through, emphasis and code marks go, and the escapes the sync
    writes for a backslash and for `[` `]` (gitlab_buzz_sync._md_escape; this is its exact inverse, done last) are the characters
    they stand for. An escape is text, never syntax, so a link's text may hold escaped brackets: one link in
    `[#132 \\[title\\]](url)`. The links are looked for in a copy of the line with every escape masked (same length, so a span
    is a span of the line too), which keeps the search linear."""
    masked = CARD_ESCAPE_RE.sub("\0\0", line)
    parts, at, links = [], 0, 0
    for link in CARD_TITLE_LINK_RE.finditer(masked):
        parts += [line[at:link.start()], line[link.start(1):link.end(1)]]
        at, links = link.end(), links + 1
    plain = "".join(parts) + line[at:]
    plain = CARD_TITLE_EMPHASIS_RE.sub(r"\2", CARD_TITLE_STRONG_RE.sub(r"\2", plain)).replace("`", "")
    return CARD_ESCAPE_RE.sub(r"\1", plain), links


def _card_title(text: str) -> tuple[str, int | None]:
    """The title of a card: the first line of the (already neutralised) text that says something, as plain text — quote,
    heading, list and task marks go, and so do the marks and escapes of _plain_markdown (bold, strike-through, emphasis, code,
    the address of a link or image, escaped brackets), one clean line (_safe_name), at most CARD_TITLE_COLUMNS display columns
    with an ellipsis (_fit_columns). Blank lines
    and a line with nothing left (a rule, a bare `>`) are passed over; a code fence ends the search, since what
    follows is code. "" when there is no such line. The second value is the index of the line the title came from when the
    title says all that line says, so a message of nothing else needs no folded text: not when the title was cut, lost the
    address of a link, or is a table row; None otherwise."""
    for index, line in enumerate(text.split("\n")):
        bare = line.strip()
        if not bare or CARD_TITLE_RULE_RE.fullmatch(bare):
            continue
        if CARD_FENCE_RE.match(line):
            break
        plain, links = _plain_markdown(CARD_TITLE_BLOCK_RE.sub("", bare, count=1))
        title = _safe_name(plain)
        if not title:
            continue
        fitted = _fit_columns(title, CARD_TITLE_COLUMNS)
        cut, title = fitted != title, fitted
        return title, None if cut or links or bare.startswith("|") else index
    return "", None


def _summary_line(text: str) -> str:
    """The text on one line, cleaned like the title (_plain_markdown) one line at a time and only as far as the summary can
    show it: CARD_SUMMARY_CHARS characters and one more, which is how the caller knows it was cut."""
    words: list[str] = []
    size = 0
    for part in text.split("\n"):
        for word in _plain_markdown(part)[0].split():
            words.append(word)
            size += len(word) + 1
        if size > CARD_SUMMARY_CHARS + 1:
            break
    return " ".join(words)


def _open_fence(text: str) -> str | None:
    """The code fence (```` ``` ```` or `~~~`, as long as it was opened) that is still open at the end of `text`."""
    fence: str | None = None
    for line in text.split("\n"):
        found = CARD_FENCE_RE.match(line)
        if fence is None:
            fence = found.group(1) if found else None
        elif found and len(found.group(1)) >= len(fence) and not line.strip().strip(fence[0]):  # fence-only, of the same kind
            fence = None
    return fence


def _trim_unfinished(prefix: str, rest: str) -> str:
    """`prefix` was cut inside a line: take back what would be left half-open on that line — a link or image, a bare
    URL, a code span, bold or strike-through, or half an escape — rather than show it broken. `rest` is what followed the cut."""
    head, newline, line = prefix.rpartition("\n")
    if not line or not rest or rest.startswith("\n") or CARD_FENCE_RE.match(line):
        return prefix
    touching = not rest[0].isspace()  # a token that ends at the cut may go on in `rest`
    masked = CARD_ESCAPE_RE.sub("\0\0", line)  # an escape (`\[`, `\]`, `\\`) is text, never syntax; same length, so a cut index means the same in both
    if masked.endswith("\\") and rest[0] in "\\[]":  # the cut fell inside an escape: its first half goes
        line, masked = line[:-1], masked[:-1]
    cuts_link = [CARD_LINK_TEXT_OPEN_RE, CARD_LINK_URL_OPEN_RE]
    while line:
        cut = min((m.start() for m in (p.search(masked) for p in cuts_link) if m), default=None)
        closed = CARD_LINK_TEXT_CLOSED_RE.search(masked)
        if closed and rest.startswith("("):
            cut = min(cut, closed.start()) if cut is not None else closed.start()
        url = CARD_BARE_URL_RE.search(masked)
        if url and touching:
            cut = min(cut, url.start()) if cut is not None else url.start()
        for marker in ("`", "**", "~~"):
            if line.count(marker) % 2:
                cut = min(cut, line.rindex(marker)) if cut is not None else line.rindex(marker)
        if cut is None:
            break
        line, masked, touching, rest = line[:cut], masked[:cut], True, "x"
    return head + newline + line


def _markdown_prefix(text: str, end: int) -> tuple[str, bool]:
    """The first `end` characters of a markdown text as something that can be shown alone: a code fence the cut left open
    is closed, and a line cut in the middle loses its half-open link, code span or emphasis. True says the result ends with
    a fence line, so whatever follows (an ellipsis) has to start a line of its own (a fence followed by text on its line is no closing fence)."""
    prefix = text[:end]
    fence = _open_fence(prefix)
    if fence is not None:
        return prefix.rstrip("\n") + "\n" + fence, True
    trimmed = _trim_unfinished(prefix, text[end:]).rstrip()
    return trimmed, CARD_FENCE_RE.match(trimmed.rpartition("\n")[2]) is not None


def _mention_token(mention: CardMention) -> str:
    """By address, else by an open_id of the sending app, else a plain name that notifies nobody. Never a union_id:
    Feishu refuses `<at id=on_…>` in a card (230099)."""
    email, open_id = mention.email, mention.open_id
    if isinstance(email, str) and len(email) <= EMAIL_MAX_LENGTH and EMAIL_RE.fullmatch(email):
        return f"<at email={email}></at>"
    if isinstance(open_id, str) and OPEN_ID_RE.fullmatch(open_id):
        return f"<at id={open_id}></at>"
    return "@" + (_card_name(mention.name) or "?")


def _plain(text: str) -> dict[str, str]:
    return {"tag": "plain_text", "content": text}


def _markdown(text: str) -> dict[str, str]:
    return {"tag": "markdown", "content": text}


def _card_byline(byline: str, open_url: str | None) -> dict[str, Any]:
    """The card's first body row: "speaker · #channel" in small grey plain text (a name never becomes markdown), and beside it
    a small link that opens the message in Buzz when there is one (a link rather than a button: the card stays short)."""
    info = {"tag": "div", "text": {"tag": "plain_text", "content": byline, "text_size": "notation", "text_color": "grey"}}
    if not open_url:
        return info
    link = {"tag": "markdown", "content": f"[{CARD_OPEN_TEXT}]({open_url})", "text_size": "notation"}
    return {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px",
            "columns": [{"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center", "elements": [info]},
                        {"tag": "column", "width": "auto", "vertical_align": "center", "elements": [link]}]}


def _card_gitlab_button(url: str) -> dict[str, Any]:
    """A fixed navigation action. `url` has already passed `_card_gitlab_url`'s structural HTTPS checks."""
    return {"tag": "button", "text": _plain(CARD_GITLAB_OPEN_TEXT), "type": "default", "size": "medium",
            "behaviors": [{"type": "open_url", "default_url": url}]}


def build_message_card(speaker: str, content: str, *, agent: bool = False, channel: str = "",
                       mentions: tuple[CardMention, ...] = (), open_url: str | None = None) -> str:
    """A Buzz message as a short Feishu card (JSON 2.0): the message's first line as the title (the speaker when there is
    none; blue for a person, green for an agent), no subtitle; the message list's one-line summary "speaker · #channel：text";
    a body of one grey line "speaker · #channel" with a small link that opens the message in Buzz (no button), the mentions
    on a line of their own, and the whole text in a panel that starts folded whenever it says more than the title. A GitLab
    sync's trailing ``状态记录`` block is the exception: it is removed from that panel and rendered visibly as the final content
    element. A structurally verified GitLab target becomes the absolute-last navigation button. Everything a user wrote is
    neutralised (card_markdown, _card_title, _card_name); the lines a GitLab -> Buzz sync
    message has for programs are in none of those places (_card_readable). The card is under MAX_CARD_BYTES whatever the
    input: past CARD_TRIM_BYTES the folded text, then an exceptionally long visible history, is cut."""
    name = _card_name(speaker) or "?"
    source = card_markdown(content)
    readable = _card_readable(source)
    gitlab_url = _card_gitlab_url(source, readable)
    readable = _card_without_gitlab_url_line(readable, gitlab_url)
    text, status_history = _card_status_history(source, readable)
    title, duplicate = _card_title(text)
    lines = text.split("\n")
    rest = "\n".join(lines[:duplicate] + lines[duplicate + 1:]) if duplicate is not None else text
    place = _card_name(channel)
    byline = f"{name} · #{place}" if place else name
    header = {"title": _plain(title or name), "template": "green" if agent else "blue"}
    line = _summary_line(text)
    summary = f"{byline}：{line[:CARD_SUMMARY_CHARS]}{'…' if len(line) > CARD_SUMMARY_CHARS else ''}"
    body = [_card_byline(byline, open_url if open_url and open_url.startswith("https://") else None)]
    if mentions:
        body.append(_markdown(" ".join(_mention_token(m) for m in mentions[:CARD_MENTIONS_MAX])))

    def encode(full: str | None, visible_status: str | None) -> str:
        rows = list(body)
        if full is not None:
            rows.append({"tag": "collapsible_panel", "expanded": False,
                         "header": {"title": _plain(CARD_EXPAND.format(n=len(text)))}, "elements": [_markdown(full)]})
        if visible_status is not None:
            rows.append(_markdown(visible_status))
        if gitlab_url is not None:
            rows.append(_card_gitlab_button(gitlab_url))
        # Feishu only permits later PATCH updates when both the old and replacement cards explicitly opt into a
        # shared update. Every mirrored card is therefore born updateable; this changes no visible content.
        doc = {"schema": CARD_SCHEMA, "config": {"summary": {"content": summary}, "update_multi": True},
               "header": header, "body": {"elements": rows}}
        return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))

    folded_text = text if rest.strip() else None
    raw = encode(folded_text, status_history)
    if len(raw.encode("utf-8")) <= CARD_TRIM_BYTES:
        return raw

    # A normal history is tiny.  Keep the most recent records visible if an abnormal one alone would exceed the card
    # budget; current status still remains near the top of the main text.  This is a visible truncation, never a fold.
    if status_history is not None and len(encode(None, status_history).encode("utf-8")) > CARD_TRIM_BYTES:
        records = status_history.split("\n")[1:]

        def recent(count: int) -> str:
            tail = records[-count:] if count else []
            return "\n".join([CARD_STATUS_HISTORY_TITLE, CARD_STATUS_TRUNCATED_NOTE, *tail])

        lo, hi = 0, len(records) + 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if len(encode(None, recent(mid)).encode("utf-8")) <= CARD_TRIM_BYTES else (lo, mid)
        status_history = recent(lo)
        raw = encode(folded_text, status_history)
        if len(raw.encode("utf-8")) <= CARD_TRIM_BYTES:
            return raw

    if folded_text is None:
        return encode(None, status_history)

    def cut(n: int) -> str:
        kept, _ = _markdown_prefix(text, n)
        return f"{kept}\n\n{CARD_TRUNCATED_NOTE}"
    lo, hi = 0, len(text)  # the most characters whose card still fits: lo fits, hi (the whole text) does not
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if len(encode(cut(mid), status_history).encode("utf-8")) <= CARD_TRIM_BYTES else (lo, mid)
    result = encode(cut(lo), status_history)
    # Nothing above can get here with a card of MAX_CARD_BYTES or more; if CARD_TRIM_BYTES were ever set wrong, the
    # folded text goes rather than a card Feishu would refuse.
    return result if len(result.encode("utf-8")) < MAX_CARD_BYTES else encode(None, status_history)


# ---------------------------------------------------------------- routing


def route_buzz_event(event: Mapping[str, Any], *, mirror_pubkey: str, agent_apps: Mapping[str, str],
                     human_pubkeys: set[str], agent_pubkeys: set[str], names: Mapping[str, str],
                     mention_targets: Mapping[str, tuple[str, str]], card: CardContext | None = None,
                     unmapped_senders: str = DEFAULT_BUZZ_UNMAPPED_SENDERS,
                     agent_mention_targets: Mapping[str, tuple[str, str]] | None = None,
                     unmanaged_agents: str = DEFAULT_BUZZ_UNMANAGED_AGENTS,
                     managed_agents: Collection[str] | None = None,
                     other_mirrors: Collection[str] = frozenset()) -> Outbound | str:
    """Decide who says a Buzz event in Feishu. A str is a skip reason. With a CardContext the event also comes as a card
    (Outbound.card); `text` is then what would have been sent without one, ready for the fallback. `agent_mention_targets`
    is the subset of mention_targets that are the channel's own agents (their own Feishu app is live): what a
    buzz_unmapped_senders "context" author's own p tags may still notify (see below). `other_mirrors` are identities
    declared another host's Feishu mirror (ADR-0020): what they say came from another group and is never said again here."""
    author = str(event.get("pubkey") or "")
    local_status = author == mirror_pubkey and any(
        isinstance(tag, list) and tag[:2] == [FEISHU_SYNC_STATUS_TAG, "membership"]
        for tag in event.get("tags") or [])
    if local_status and str(event.get("kind")) in MESSAGE_KINDS.split(","):
        # This mirror normally never echoes itself. A membership status is deliberately canonical in Buzz and delivered
        # to this mirror's bound group once; another host still recognizes the author as `other_mirrors` and skips it.
        content = _mark_forged_lines(FEISHU_AT_MARKUP_RE.sub(r"＜\1", str(event.get("content") or "")),
                                     FORGED_FEISHU_SIGNATURE_RE)
        return Outbound(str(event.get("id") or ""), None, content, None)
    if author == mirror_pubkey:
        return "echo"
    if author in other_mirrors:
        return "other_mirror"
    if str(event.get("kind")) not in MESSAGE_KINDS.split(","):
        return "kind"
    images, images_over, attachment_urls = event_images(event)
    content = _markdown_without_attachments(str(event.get("content") or ""), attachment_urls)
    if not content.strip():
        if not images:
            return "empty"
        content = "[图片]"  # the text message is the event's body in Feishu (threads and the ledger hang on it)
    raw_content = content  # what a card is built from: the same words as the text, whichever way it is sent
    content = _mark_forged_lines(FEISHU_AT_MARKUP_RE.sub(r"＜\1", content), FORGED_FEISHU_SIGNATURE_RE)
    event_id = str(event.get("id") or "")
    parent = _buzz_parent(event)
    if author in agent_apps:
        # The agent's own bot: open_ids of the owner's app mean nothing there.
        speaker = names.get(author) or author[:12]
        return Outbound(event_id, agent_apps[author], content, parent,
                        _event_card(event, raw_content, speaker, True, names, card) if card else None, images, images_over)
    if author not in human_pubkeys:
        if author in agent_pubkeys:
            # Only an agent this host has no configuration for can be relayed (config buzz_unmanaged_agents "relay"):
            # its credentials live with its own owner and will never be here, so the alternative is dropping it
            # forever. A configured agent without a live bot this round is left alone: that state self-heals once
            # reconcile puts its bot in the group, and relaying meanwhile would make one agent speak in two voices.
            # `managed_agents` unknown (None) is read as "all of them are managed", so nothing is relayed by accident.
            if unmanaged_agents != "relay" or managed_agents is None or author in managed_agents:
                return "agent_bot_unavailable"
            # Said through the Desk like a human's, signed distinctly. No p tag becomes an <at> and the card
            # names nobody: the agent's own bot never renders mentions either (open_ids belong to one app), so
            # relaying must not make an agent's message notify more people than it would have itself (skills#143).
            name = _safe_name(names.get(author) or "") or author[:12]
            return Outbound(event_id, None, f"{name}（Buzz·{BUZZ_AGENT_RELAY_LABEL}）：{content}", parent,
                            _event_card(event, raw_content, f"{name}（{BUZZ_AGENT_RELAY_LABEL}）", False, names, card,
                                        mention_pubkeys=frozenset()) if card else None,
                            images, images_over, relayed=True)
        if unmapped_senders != "context":
            return "not_channel_human"
        # An author that is neither a verified channel human nor a configured agent (config buzz_unmapped_senders
        # "context"): said through the Desk like a human's, signed distinctly so nobody takes it for a member's.
        # Symmetric with route_feishu_message's stranger: a mention of one of the channel's own agents still becomes a
        # real <at> (he can wake it, agent_mention_targets), but any other target (a human, or an agent with no live
        # Feishu app) is silently dropped, never a real notification — and never left as readable text either: the
        # literal "@Name" the author typed for that target is neutralised (full-width ＠) in both the text and the
        # card, the same way a stranger's typed mentions are neutralised on the Feishu -> Buzz side. A stranger's
        # message must not even visually address a real person by name (ADR-0017, skills#142).
        agent_targets = agent_mention_targets or {}
        name = _safe_name(names.get(author) or "") or author[:12]
        ats: list[str] = []
        disallowed_names: set[str] = set()
        seen: set[str] = {author}
        for tag in event.get("tags") or []:
            if not (isinstance(tag, list) and len(tag) >= 2 and tag[0] == "p"):
                continue
            target = tag[1]
            if target in seen:
                continue
            seen.add(target)
            if target in agent_targets:
                feishu_id, label = agent_targets[target]
                ats.append(f'<at user_id="{feishu_id}">{_safe_name(label) or target[:12]}</at>')
            elif names.get(target):
                disallowed_names.add(names[target])

        def scrub(text: str) -> str:
            for disallowed in disallowed_names:
                text = re.sub(r"@" + re.escape(disallowed) + r"\b", "＠" + disallowed, text)
            return text

        content, raw_content = scrub(content), scrub(raw_content)
        text = f"{name}（Buzz·{BUZZ_CONTEXT_LABEL}）：{content}"
        if ats:
            text += " " + " ".join(ats)
        card_json = (_event_card(event, raw_content, f"{name}（{BUZZ_CONTEXT_LABEL}）", False, names, card,
                                 mention_pubkeys=frozenset(agent_targets)) if card else None)
        return Outbound(event_id, None, text, parent, card_json, images, images_over)
    name = _safe_name(names.get(author) or "") or author[:12]
    ats: list[str] = []
    seen: set[str] = set()
    for tag in event.get("tags") or []:
        if not (isinstance(tag, list) and len(tag) >= 2 and tag[0] == "p"):
            continue
        target = tag[1]
        if target in seen or target == author or target not in mention_targets:
            continue
        seen.add(target)
        feishu_id, label = mention_targets[target]
        ats.append(f'<at user_id="{feishu_id}">{_safe_name(label) or target[:12]}</at>')
    text = f"{name}（Buzz）：{content}"
    if ats:
        text += " " + " ".join(ats)
    return Outbound(event_id, None, text, parent, _event_card(event, raw_content, name, False, names, card) if card else None,
                    images, images_over)


def _event_card(event: Mapping[str, Any], raw_content: str, speaker: str, agent: bool, names: Mapping[str, str],
                card: CardContext, *, mention_pubkeys: frozenset[str] | None = None) -> str:
    """The card of a Buzz event. Its p tags are named by address, else (only when the owner's app bot sends, since
    open_ids belong to one app) by open_id, else by a plain name; the author and repeats are left out. `mention_pubkeys`
    restricts which targets become a mention at all (a buzz_unmapped_senders "context" author: its own configured
    agents only, never a human — the ones left out are not even named in plain text, the same as route_feishu_message's
    stranger drops them)."""
    author = str(event.get("pubkey") or "")
    mentions: list[CardMention] = []
    seen = {author}
    for tag in event.get("tags") or []:
        if not (isinstance(tag, list) and len(tag) >= 2 and tag[0] == "p" and isinstance(tag[1], str)):
            continue
        target = tag[1]
        if target in seen or not HEX64_RE.fullmatch(target):
            continue
        if mention_pubkeys is not None and target not in mention_pubkeys:
            continue
        seen.add(target)
        mentions.append(CardMention(names.get(target) or target[:12], card.emails.get(target),
                                    None if agent else card.open_ids.get(target)))
    url = open_link(card.link_base, str(event.get("id") or ""), card.channel_id, buzz_thread_root(event))
    return build_message_card(speaker, raw_content, agent=agent, channel=card.channel_name,
                              mentions=tuple(mentions), open_url=url)


def _reaction_emoji(content: Any) -> str:
    return str(content or "").translate({ord(c): None for c in EMOJI_SELECTORS}).strip()


def reverse_reaction_map(mapping: Mapping[str, str]) -> dict[str, str]:
    """Feishu emoji_type -> the emoji said in Buzz (ADR-0020): the first emoji that maps to it (THUMBSUP is 👍, not "+")."""
    out: dict[str, str] = {}
    for emoji, emoji_type in mapping.items():
        out.setdefault(emoji_type, _reaction_emoji(emoji))
    return out


def merged_reaction_map(configured: Mapping[str, str]) -> dict[str, str]:
    return {**DEFAULT_REACTION_MAP, **{_reaction_emoji(k): v for k, v in configured.items()}}


def reaction_target(event: Mapping[str, Any]) -> str | None:
    """A kind 7 carries one bare e tag: the event it reacts to."""
    for tag in event.get("tags") or []:
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "e" and isinstance(tag[1], str) and HEX64_RE.fullmatch(tag[1]):
            return tag[1]
    return None


def edit_target(event: Mapping[str, Any]) -> str | None:
    """A kind 40003 replaces exactly one event, named by its sole valid bare e tag."""
    targets = [tag[1] for tag in event.get("tags") or []
               if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "e"
               and isinstance(tag[1], str) and HEX64_RE.fullmatch(tag[1])]
    return targets[0] if len(targets) == 1 else None


def compact_edit_revision(event: Mapping[str, Any]) -> int:
    """The producer's causal revision for a GitLab compact overlay; zero keeps legacy ordering."""

    if int(event.get("kind") or 0) != EDIT_KIND:
        return 0
    lines = str(event.get("content") or "").splitlines()
    if not lines:
        return 0
    header = lines[0] if lines[0].startswith(CARD_NOTIFY_HEADER) else (
        lines[-1] if lines[-1].startswith(CARD_NOTIFY_HEADER) else ""
    )
    match = COMPACT_EDIT_REV_RE.search(header)
    return int(match.group(1)) if match is not None else 0


def buzz_message_order(event: Mapping[str, Any]) -> tuple[int, int, str, int, str]:
    """Apply an original before same-second edits, then compact edits in causal revision order."""

    created = int(event.get("created_at") or 0)
    event_id = str(event.get("id") or "")
    if int(event.get("kind") or 0) == EDIT_KIND:
        return created, 1, edit_target(event) or "", compact_edit_revision(event), event_id
    return created, 0, "", 0, event_id


def buzz_reaction_order(event: Mapping[str, Any]) -> tuple[int, int, str]:
    """At one Nostr second remove an old reaction before adding its replacement."""

    return (
        int(event.get("created_at") or 0),
        0 if str(event.get("kind")) == "5" else 1,
        str(event.get("id") or ""),
    )


def route_buzz_reaction(event: Mapping[str, Any], *, agent_apps: Mapping[str, str], human_pubkeys: set[str],
                        agent_pubkeys: set[str], reaction_map: Mapping[str, str], relay_authors: Collection[str] = frozenset(),
                        proxy_app_id: str | None = None, mirror_pubkey: str | None = None,
                        other_mirrors: Collection[str] = frozenset()) -> tuple[str, str] | str:
    """Which app's bot puts which Feishu emoji on the target. An agent with its own bot reacts from it; an author in
    `relay_authors` (reaction_sync "two_way": the channel's people and agents relayed through the Desk) from the
    configured Desk bot. Without `relay_authors` a human's reaction is not synced ("agents_only"). The mirror's reactions came from
    Feishu and never go back; another host's mirror's came from another group. A str is a skip reason."""
    if str(event.get("kind")) != "7":
        return "kind"
    author = str(event.get("pubkey") or "")
    if mirror_pubkey and author == mirror_pubkey:
        return "echo"
    if author in other_mirrors:
        return "other_mirror"
    if author not in agent_apps and author in relay_authors and proxy_app_id:
        if reaction_target(event) is None:
            return "reaction_no_target"
        emoji_type = reaction_map.get(_reaction_emoji(event.get("content")))
        return (proxy_app_id, emoji_type) if emoji_type is not None else "reaction_emoji_unmapped"
    if author in human_pubkeys:
        return "reaction_human"
    if author not in agent_apps:
        return "agent_bot_unavailable" if author in agent_pubkeys else "reaction_not_agent"
    if reaction_target(event) is None:
        return "reaction_no_target"
    emoji_type = reaction_map.get(_reaction_emoji(event.get("content")))
    if emoji_type is None:
        return "reaction_emoji_unmapped"
    return agent_apps[author], emoji_type


def _parse_feishu_time(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value), FEISHU_TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _feishu_ts(msg: Mapping[str, Any]) -> int | None:
    created = _parse_feishu_time(msg.get("create_time"))
    return int(created.timestamp()) if created else None


def _context_mentions(mentions: Any, bot_member_to_pubkey: Mapping[str, str]) -> tuple[str, ...]:
    """A stranger can wake an agent, never a person: only the selected mention entities that are one of the channel's bots
    (matched directly by member id, the same space the row's mentions are already in — no per-app id resolution needed, so
    this costs no Feishu call). A human he @-mentioned, agent or not configured with a Feishu app, is silently dropped —
    his words are read, never honoured as a command to notify somebody. Order is the message's, duplicates collapse."""
    out: list[str] = []
    for mention in mentions or []:
        target = bot_member_to_pubkey.get(str((mention or {}).get("id") or ""))
        if target and target not in out:
            out.append(target)
    return tuple(out)


def _context_inbound(msg: Mapping[str, Any], sender: Mapping[str, Any], content: str, image_keys: list[str], now: datetime,
                     bot_member_to_pubkey: Mapping[str, str]) -> Inbound:
    """A Feishu message from a person who maps to no channel member (config feishu_unmapped_senders "context"): words to
    read, not a call to act on a human — but PO decision 2026-09-22: he can still wake an agent (see _context_mentions),
    because the channel's own bots are not a person to command, only a worker to point at. Signed with its own label so
    nobody takes it for a member's; the body's typed @ and nostr: are neutralised like a member's, so the Buzz CLI cannot
    turn them into a mention of anybody else. Images keep their keys and later use the same validated, metadata-stripping,
    size-limited path as a member's images."""
    name = _safe_name(LONE_SURROGATE_RE.sub("\ufffd", str(sender.get("name") or "")))[:CONTEXT_NAME_LIMIT].rstrip() \
        or CONTEXT_SENDER_FALLBACK_NAME
    content = LONE_SURROGATE_RE.sub("\ufffd", content)  # a lone surrogate cannot be encoded: the CLI call would raise
    if len(content) > CONTEXT_BODY_LIMIT:  # a stranger cannot make one message the relay refuses (and the round need attention)
        content = content[:CONTEXT_BODY_LIMIT].rstrip() + f"\n（正文过长，已截断，原文 {len(content)} 字）"
    body = _mark_forged_lines(sync.neutralize(BIDI_CONTROLS_RE.sub("", content)), FORGED_CONTEXT_SIGNATURE_RE,
                              _context_signature_probe)
    text = f"{CONTEXT_SENDER_LABEL} {name}：{body}"
    created = _parse_feishu_time(msg.get("create_time"))
    if created is not None and (now - created).total_seconds() > STALE_AFTER_SECONDS:
        text += f"（飞书 {msg.get('create_time')}）"
    mentions = _context_mentions(msg.get("mentions"), bot_member_to_pubkey)
    # Last word: whatever was done to the text on the way, what goes to the CLI has no typed @ and no nostr: only the
    # selected mentions above can notify anybody, and only agents are ever in that list.
    return Inbound(str(msg.get("message_id") or ""), "", sync.neutralize(text), mentions, tuple(image_keys), True)


def route_feishu_message(msg: Mapping[str, Any], *, open_id_to_pubkey: Mapping[str, str],
                         bot_member_to_pubkey: Mapping[str, str], channel_members: set[str],
                         names: Mapping[str, str], now: datetime,
                         resolve_id: Callable[[str], str] | None = None,
                         allowed_senders: frozenset[str] | None = None,
                         unmapped_senders: str = DEFAULT_UNMAPPED_SENDERS,
                         ambiguous_ids: frozenset[str] = frozenset()) -> Inbound | str:
    """Decide what a Feishu message becomes in Buzz. A str is a skip reason. `open_id_to_pubkey` is keyed by the
    people's ids in the chosen identity space; in union mode a message row still carries this app's open_ids, so
    `resolve_id` turns one into a union_id ("" when it cannot be vouched for) — only when a person is actually
    looked up, so a message that is skipped anyway costs nothing."""
    if msg.get("deleted"):
        return "deleted"
    if msg.get("msg_type") == "system":
        return "system"
    sender = msg.get("sender") or {}
    if sender.get("sender_type") != "user":
        return "bot"
    content = str(msg.get("content") or "")
    if not content.strip():
        return "empty"  # before anyone is looked up: a message that says nothing is never worth a Feishu call
    image_keys: list[str] = []

    def take_image(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2)
        if key not in image_keys:
            image_keys.append(key)
        return ""

    without_images = FEISHU_IMAGE_MARK_RE.sub(take_image, content)
    if image_keys:  # the pictures travel as attachments; what is left is the text (or a placeholder when there is none)
        content = re.sub(r"\n{3,}", "\n\n", without_images).strip() or "[图片]"
    sender_id = str(sender.get("id") or "")
    resolved = resolve_id(sender_id) if resolve_id else sender_id
    pubkey = open_id_to_pubkey.get(resolved)
    if not pubkey:
        # Only a person Feishu vouched for can be a stranger: a real open_id whose id in the chosen space is known (union mode: ""
        # means nobody vouched for him). A sender allowlist never lets strangers in.
        if (unmapped_senders != "context" or allowed_senders is not None or not resolved
                or not OPEN_ID_RE.fullmatch(sender_id)):
            return "unmapped_sender"
        if resolved in ambiguous_ids:  # some member's account, only nobody can say whose: not a stranger, not anybody
            return "identity_conflict"
        return _context_inbound(msg, sender, content, image_keys, now, bot_member_to_pubkey)
    if pubkey not in channel_members:
        return "sender_not_in_channel"
    if allowed_senders is not None and pubkey not in allowed_senders:
        # After the identity checks, before anything else about the message is looked up (mentions included): the list
        # only narrows whose words reach Buzz, it never vouches for anyone and never limits who can be @-mentioned.
        return "sender_not_allowed"
    name = _safe_name(str(names.get(pubkey) or sender.get("name") or "")) or pubkey[:12]
    # Only selected mention entities notify (via --mention); typed @ and nostr: stay text.
    # The bidi controls come out before the neutralising: they are removed from the text anyway, and a `nostr\u202a:npub…` that was
    # neutralised while they were still in it turned back into a mention when they went.
    body = _mark_forged_lines(sync.neutralize(BIDI_CONTROLS_RE.sub("", content)), FORGED_BUZZ_SIGNATURE_RE)
    text = f"[飞书] {name}：{body}"
    created = _parse_feishu_time(msg.get("create_time"))
    if created is not None and (now - created).total_seconds() > STALE_AFTER_SECONDS:
        text += f"（飞书 {msg.get('create_time')}）"
    text = sync.neutralize(text)
    mentions: list[str] = []
    for mention in msg.get("mentions") or []:
        mid = str((mention or {}).get("id") or "")
        target = bot_member_to_pubkey.get(mid) or open_id_to_pubkey.get(resolve_id(mid) if resolve_id else mid)
        if target and target != pubkey and target in channel_members and target not in mentions:
            mentions.append(target)
    return Inbound(str(msg.get("message_id") or ""), pubkey, text, tuple(mentions), tuple(image_keys))


# ---------------------------------------------------------------- pairing ids (union mode)


def _row_people(row: Mapping[str, Any], skip: set[str]) -> dict[str, list[Mapping[str, Any]]]:
    """The people of a list row — the sender and every mentioned person — by open_id, with the mention entries each
    one appears in (none for the sender). Bots (`skip`) and non-person mentions such as @all are not people."""
    people: dict[str, list[Mapping[str, Any]]] = {}
    sender = row.get("sender") if isinstance(row.get("sender"), dict) else {}
    sid = str(sender.get("id") or "")
    if sender.get("sender_type") == "user" and OPEN_ID_RE.fullmatch(sid):
        people.setdefault(sid, [])
    for mention in row.get("mentions") or []:
        mid = str(mention.get("id") or "") if isinstance(mention, dict) else ""
        if OPEN_ID_RE.fullmatch(mid) and mid not in skip:
            people.setdefault(mid, []).append(mention)
    return people


def pair_ids(row: Mapping[str, Any], view: Mapping[str, Any] | None, skip: set[str]) -> tuple[dict[str, str], set[str]]:
    """Pair the people of a message row (this app's open_ids) with their union_ids using the same message fetched
    as union_id: the sender with the view's sender, a mention with the view's mention of the same `key` — nothing
    else, never a name or a position. Returns (open_id -> union_id for the ones Feishu vouches for, the open_ids
    that are contradicted inside this very answer). Anything the answer cannot vouch for is simply absent."""
    people = _row_people(row, skip)
    paired: dict[str, str] = {}
    contradicted: set[str] = set()

    def vouch(open_id: str, union_id: Any, id_type: Any) -> None:
        if id_type != "union_id" or not isinstance(union_id, str) or not UNION_ID_RE.fullmatch(union_id):
            return
        if paired.get(open_id, union_id) != union_id:
            contradicted.add(open_id)
        paired[open_id] = union_id

    if isinstance(view, Mapping) and view.get("message_id") == row.get("message_id"):
        sender = row.get("sender") if isinstance(row.get("sender"), dict) else {}
        view_sender = view.get("sender")
        sid = str(sender.get("id") or "")
        if sid in people and isinstance(view_sender, dict) and view_sender.get("sender_type") == "user":
            vouch(sid, view_sender.get("id"), view_sender.get("id_type"))
        in_view: dict[str, list[Mapping[str, Any]]] = {}
        for mention in view.get("mentions") or []:
            if isinstance(mention, dict):
                in_view.setdefault(str(mention.get("key") or ""), []).append(mention)
        in_row = [str(m.get("key") or "") for m in row.get("mentions") or [] if isinstance(m, dict)]
        for open_id, mentions in people.items():
            for mention in mentions:
                key = str(mention.get("key") or "")
                if key and in_row.count(key) == 1 and len(in_view.get(key, [])) == 1:  # a key that is not unique proves nothing
                    vouch(open_id, in_view[key][0].get("id"), in_view[key][0].get("id_type"))
    by_union: dict[str, set[str]] = {}
    for open_id, union_id in paired.items():
        by_union.setdefault(union_id, set()).add(open_id)
    for opens in by_union.values():  # two people in this app cannot share one union_id
        if len(opens) > 1:
            contradicted |= opens
    return {o: u for o, u in paired.items() if o not in contradicted}, contradicted


class IdResolver:
    """Turns this app's open_ids of one Feishu message into union_ids (union mode). A cached pair answers at once;
    otherwise the message is fetched once as union_id and paired (pair_ids), and what the answer vouches for is
    cached in state.idmap. When the answer contradicts the cache, neither side is trusted: the stale entries go, this
    answer is not adopted, and the message is skipped this round (identity_conflict). A failed fetch (`error`) means
    nothing was decided: the message is retried like a refused send."""

    def __init__(self, owner: LarkCli, state: State, report: dict[str, Any], row: Mapping[str, Any], bot_ids: set[str]) -> None:
        self.owner, self.state, self.report, self.row, self.bot_ids = owner, state, report, row, bot_ids
        self.looked_up = False
        self.error = False
        self.conflict = False
        self.unpaired: set[str] = set()  # the people this message could not vouch for

    def __call__(self, open_id: str) -> str:
        cached = self.state.idmap.get(open_id)
        if cached is not None:
            return cached
        if not OPEN_ID_RE.fullmatch(open_id) or open_id in self.bot_ids:
            return ""
        self._lookup()
        return self.state.idmap.get(open_id, "")  # nothing is adopted when the answer contradicts the cache

    def _lookup(self) -> None:
        if self.looked_up:
            return
        self.looked_up = True
        try:
            view = self.owner.message_view(str(self.row.get("message_id") or ""), "union_id")
        except CliError:
            self.error = True
            return
        idmap = self.state.idmap
        paired, contradicted = pair_ids(self.row, view, self.bot_ids)
        stale = {o for o in contradicted if o in idmap}
        for open_id, union_id in paired.items():
            if idmap.get(open_id, union_id) != union_id:  # the same open_id, another union_id
                stale.add(open_id)
            stale |= {o for o, u in idmap.items() if u == union_id and o != open_id}  # the same union_id, another open_id
        if stale or contradicted:
            for open_id in stale:
                del idmap[open_id]
            self.conflict = True
            self.report["identity_conflicts"] += 1
            return
        idmap.update(paired)
        self.unpaired = {o for o in _row_people(self.row, self.bot_ids) if o not in idmap}

    def sender_unpaired(self) -> bool:
        sender = self.row.get("sender") if isinstance(self.row.get("sender"), dict) else {}
        return str(sender.get("id") or "") in self.unpaired

    def mentions_unpaired(self) -> int:
        """Mentioned people this message could not vouch for (a sender that could not be is skipped, never counted here)."""
        return len(self.unpaired)


# ---------------------------------------------------------------- state


def _mark(kind: str, first_attempt: int, extra: str | None = None) -> str:
    """kind:<first attempt>[:extra]. The first attempt time survives every retry, so the
    idempotency window is always counted from the first send."""
    return f"{kind}:{first_attempt}" + (f":{extra}" if extra is not None else "")


def _marked_extra(value: str) -> str | None:
    parts = value.split(":", 2)
    return parts[2] if len(parts) == 3 else None


def _send_extra(parent: str | None, mode: str) -> str:
    """What a Buzz -> Feishu ledger mark remembers of the first attempt: the Feishu message it replies to ("-" for none) and
    how it was sent (SEND_CARD, SEND_TEXT_FALLBACK, or "" for plain text)."""
    return (parent or "-") + ("," + mode if mode else "")


def _parse_send_extra(extra: str | None) -> tuple[str | None, str]:
    parent, _, mode = (extra or "-").partition(",")
    return (None if parent in ("", "-") else parent), (mode if mode in (SEND_CARD, SEND_TEXT_FALLBACK) else "")


def _edit_extra(target: str, message_id: str, mode: str) -> str:
    return f"{target}|{message_id}|{mode}"


def card_refused(exc: CliError) -> bool:
    """Feishu said no to the card itself (an api or validation refusal that is not a rate limit): it did not go out."""
    return (exc.definite and exc.error_type in CARD_REFUSAL_TYPES and exc.code not in LARK_RATE_LIMIT_CODES
            and exc.kind != "rate_limited")


def _is_pending(value: str | None) -> bool:
    return bool(value) and (value == PENDING or value.startswith(PENDING + ":"))


def _is_retry(value: str | None) -> bool:
    return bool(value) and value.startswith(RETRY + ":")


def _marked_time(value: str) -> int:
    try:
        return int(value.split(":", 2)[1])
    except (IndexError, ValueError):
        return 0


def _agent_intro_value_ok(value: str) -> bool:
    return (value in ("baseline", FAILED, UNKNOWN)
            or MESSAGE_ID_RE.fullmatch(value) is not None
            or re.fullmatch(r"(?:pending|retry):[0-9]{1,12}", value) is not None)


def _settled(value: str | None) -> bool:
    """A real id on the other side, not a bookkeeping marker."""
    return bool(value) and not _is_pending(value) and not _is_retry(value) and value not in (FAILED, UNKNOWN, SKIPPED)


def feishu_id_for_buzz(state: State, event_id: str) -> str | None:
    value = state.b2f.get(event_id)
    if _settled(value):
        return value
    for message_id, mirrored in state.f2b.items():
        if mirrored == event_id:
            return message_id
    return None


def buzz_id_for_feishu(state: State, message_id: str) -> str | None:
    value = state.f2b.get(message_id)
    if _settled(value):
        return value
    for event_id, mirrored in state.b2f.items():
        if mirrored == message_id:
            return event_id
    return None


def prune_state(state: State) -> None:
    if len(state.member_events) > MEMBER_EVENTS_MAX:
        # Unknown writes cannot be evicted safely: doing so would let a later round generate a second event id.
        raise GroupSyncError("the pending member event ledger exceeds its safe limit")
    if set(state.member_event_blocks) - set(state.member_events):
        raise GroupSyncError("the member event block ledger has no matching pending event")
    for ledger in (state.b2f, state.f2b, state.e2f, state.attempts, state.r2f):
        for key in list(ledger)[:max(len(ledger) - LEDGER_KEEP, 0)]:
            del ledger[key]
    state.b2f_modes = {event_id: mode for event_id, mode in state.b2f_modes.items() if event_id in state.b2f}
    state.b2f_senders = {event_id: app for event_id, app in state.b2f_senders.items() if event_id in state.b2f}
    # An image that is still open (pending, or waiting for a retry) keeps its ledger entry — its 45-minute window and its number of
    # attempts live there — and so do the other entries of its event (where the text went, the count beyond the limit): what is
    # cut is the oldest of the rest, the ones with a result.
    open_events = {item.rsplit(":", 1)[0] for item in state.img_unresolved}
    evictable = [key for key in state.images if key.rsplit(":", 1)[0] not in open_events]
    for key in evictable[:max(len(state.images) - LEDGER_KEEP, 0)]:
        del state.images[key]
    if len(state.threads) > THREAD_KEEP:
        keep = sorted(state.threads.items(), key=lambda kv: kv[1], reverse=True)[:THREAD_KEEP]
        state.threads = dict(sorted(keep, key=lambda kv: kv[1]))
    for cache in (state.idmap, state.emailmap):
        for key in list(cache)[:max(len(cache) - IDMAP_KEEP, 0)]:
            del cache[key]
    state.polled = {root: t for root, t in state.polled.items() if root in state.threads}
    state.tried = {root: t for root, t in state.tried.items() if root in state.threads}
    for key in list(state.people_seen)[:max(len(state.people_seen) - PEOPLE_KEEP, 0)]:
        del state.people_seen[key]
    for key in list(state.f2r)[:max(len(state.f2r) - LEDGER_KEEP, 0)]:
        del state.f2r[key]
    newest_note = max(state.member_notes.values(), default=0)
    state.member_notes = {k: t for k, t in state.member_notes.items() if newest_note - t <= MEMBER_NOTE_TTL}


def load_state(state_dir: Path) -> State:
    path = Path(state_dir) / STATE_FILE
    if not path.exists():
        return State()
    # A corrupt state must not read as empty: that would re-mirror everything.
    data = _load_json(_read_owner_only(path, "state file"), "state file")
    if not isinstance(data, dict):
        raise GroupSyncError("state file must be a JSON object")
    reaction_fields, identity_fields, image_fields = {"r2f", "react_since"}, {"idmap", "emailmap"}, {"images", "img_unresolved"}
    edit_fields = {"b2f_modes", "e2f", "edit_unresolved"}
    two_way_fields = {"members_synced", "feishu_seen", "buzz_seen", "member_notes", "member_events",
                      "member_event_stream", "member_event_seq", "member_event_blocks", "people_seen", "rwatch", "f2r"}
    notice_fields = {"member_notice_event", "member_notice_feishu", "member_notice_content", "member_notice_active"}
    intro_fields = {"agent_intros_initialized", "agent_intros"}
    if not reaction_fields & set(data):
        # Written before reactions were synced: start the reaction cursor at the message cursor, so
        # upgrading does not replay every old reaction.
        data = {**data, "r2f": {}, "react_since": data.get("buzz_since", 0)}
    if not identity_fields & set(data):
        # Written before the id caches: they only ever save Feishu calls, so they start empty.
        data = {**data, "idmap": {}, "emailmap": {}}
    if not image_fields & set(data):
        # Written before images were synced: nothing was ever sent as an image, so the image ledger starts empty.
        data = {**data, "images": {}, "img_unresolved": {}}
    if not edit_fields & set(data):
        # Older cards were not born with update_multi=true and their actual send mode was not retained. They are
        # deliberately not guessed: only messages sent by an edit-aware release can be updated safely.
        data = {**data, "b2f_modes": {}, "e2f": {}, "edit_unresolved": {}}
    if "b2f_senders" not in data:
        # Pre-Desk releases used the owner app for proxy sends. Existing agent messages still use their own app.
        # Do not guess an app for an old entry: it is terminal for outbound writes.
        data = {**data, "b2f_senders": {}}
    if not two_way_fields & set(data):
        # Written before the two-way sync (ADR-0020): no snapshot yet, so the next round only records one.
        data = {**data, "members_synced": 0, "feishu_seen": {}, "buzz_seen": {}, "member_notes": {}, "member_events": {},
                "member_event_stream": "", "member_event_seq": 0, "member_event_blocks": {}, "people_seen": {},
                "rwatch": {}, "f2r": {}}
    member_ledger_fields = {"member_events", "member_event_stream", "member_event_seq", "member_event_blocks"}
    present_member_ledger_fields = member_ledger_fields & set(data)
    if not present_member_ledger_fields:
        # Written by the first two-way release: snapshots already exist, but no member-event retry ledger did.
        data = {**data, "member_events": {}, "member_event_stream": "", "member_event_seq": 0,
                "member_event_blocks": {}}
    elif present_member_ledger_fields == {"member_events"}:
        # Written before membership events had a producer order and persistent retry stops. A non-empty old ledger
        # cannot be upgraded safely because its signed event has no sequence tag; validation below fails closed.
        data = {**data, "member_event_stream": "", "member_event_seq": 0, "member_event_blocks": {}}
    elif present_member_ledger_fields == member_ledger_fields - {"member_event_stream"}:
        # The immediately previous release already had ordered exact retries. A settled ledger can upgrade lazily:
        # the next operation creates its stream. Any old pending event lacks that signed tag and fails validation below.
        data = {**data, "member_event_stream": ""}
    if not notice_fields & set(data):
        data = {**data, "member_notice_event": "", "member_notice_feishu": "", "member_notice_content": "",
                "member_notice_active": False}
    if "member_notice_sender" not in data:
        data = {**data, "member_notice_sender": ""}
    if not intro_fields & set(data):
        # Existing bots predate this feature: the next verified round records them as the baseline and does not spam the group.
        data = {**data, "agent_intros_initialized": False, "agent_intros": {}}
    if "agent_intro_senders" not in data:
        data = {**data, "agent_intro_senders": {}}
    if set(data) != set(State.__dataclass_fields__):
        # A partial state would read as "nothing sent yet" and re-mirror recent messages.
        raise GroupSyncError("state file has missing or unknown fields")
    state = State(**data)
    str_maps = (state.b2f, state.b2f_modes, state.b2f_senders, state.e2f, state.f2b, state.r2f, state.idmap, state.emailmap, state.images,
                state.feishu_seen, state.buzz_seen, state.people_seen, state.rwatch, state.f2r, state.agent_intros,
                state.agent_intro_senders)
    int_maps = (state.attempts, state.threads, state.polled, state.tried, state.unresolved, state.f_unresolved,
                state.edit_unresolved, state.img_unresolved, state.member_notes)
    ok = (isinstance(state.binding, str)
          and isinstance(state.member_notice_event, str) and isinstance(state.member_notice_feishu, str)
          and isinstance(state.member_notice_content, str) and isinstance(state.member_notice_active, bool)
          and isinstance(state.member_notice_sender, str)
          and isinstance(state.agent_intros_initialized, bool)
          and all(isinstance(v, int) and not isinstance(v, bool) for v in (state.floor, state.buzz_since, state.feishu_since, state.buzz_floor, state.feishu_floor, state.react_since, state.members_synced))
          and all(isinstance(m, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in m.items()) for m in str_maps)
          and all(isinstance(m, dict) and all(isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
                                              for k, v in m.items()) for m in int_maps))
    ok = (ok and all(OPEN_ID_RE.fullmatch(k) and UNION_ID_RE.fullmatch(v) for k, v in state.idmap.items())
          and all(HEX64_RE.fullmatch(k) and (OPEN_ID_RE.fullmatch(v) or re.fullmatch(r"miss:[0-9]{1,12}", v))
                  for k, v in state.emailmap.items())
          and all(HEX64_RE.fullmatch(k) and v in (SEND_CARD, SEND_TEXT_FALLBACK) for k, v in state.b2f_modes.items()))
    ok = ok and all(HEX64_RE.fullmatch(k) and APP_ID_RE.fullmatch(v) and k in state.b2f
                    for k, v in state.b2f_senders.items())
    ok = ok and (not state.member_notice_sender or APP_ID_RE.fullmatch(state.member_notice_sender) is not None)
    ok = ok and all(HEX64_RE.fullmatch(k) and APP_ID_RE.fullmatch(v) and k in state.agent_intros
                    for k, v in state.agent_intro_senders.items())
    ok = (ok and (not state.member_notice_event or HEX64_RE.fullmatch(state.member_notice_event))
          and (not state.member_notice_feishu or MESSAGE_ID_RE.fullmatch(state.member_notice_feishu))
          and all(HEX64_RE.fullmatch(pubkey) and _agent_intro_value_ok(value)
                  for pubkey, value in state.agent_intros.items())
          and isinstance(state.member_events, dict) and len(state.member_events) <= MEMBER_EVENTS_MAX
          and isinstance(state.member_event_stream, str)
          and (not state.member_event_stream or HEX64_RE.fullmatch(state.member_event_stream) is not None)
          and isinstance(state.member_event_seq, int) and not isinstance(state.member_event_seq, bool)
          and 0 <= state.member_event_seq <= MEMBER_EVENT_SEQUENCE_MAX
          and (not state.member_event_stream or state.member_event_seq > 0)
          and isinstance(state.member_event_blocks, dict)
          and all(isinstance(operation, str) and operation in state.member_events
                  and isinstance(reason, str) and reason in MEMBER_EVENT_BLOCK_REASONS
                  for operation, reason in state.member_event_blocks.items())
          and all(_member_event_record_ok(operation, event, state.binding)
                  and member_event_stream(event) == state.member_event_stream
                  and member_event_sequence(event) <= state.member_event_seq
                  for operation, event in state.member_events.items())
          and len({member_event_sequence(event) for event in state.member_events.values()}) == len(state.member_events))
    if not ok:
        raise GroupSyncError("state file has malformed fields")
    return state


def save_state(state_dir: Path, state: State) -> None:
    _write_owner_only(Path(state_dir) / STATE_FILE, json.dumps(asdict(state), ensure_ascii=False, separators=(",", ":")))


# ---------------------------------------------------------------- env and config


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in _read_owner_only(Path(path), "mirror env file").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or key not in BUZZ_ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def child_env(base: Mapping[str, str], extra: Mapping[str, str]) -> dict[str, str]:
    env = {key: base[key] for key in CHILD_ENV_KEYS if key in base}
    env.update(extra)
    return env


def _absolute(value: Any, what: str) -> str:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise GroupSyncError(f"config {what} must be an absolute path")
    return value


def _check_people_api(api: Any) -> None:
    """people_api is exactly {base_url, signer_env_file}: base_url an https origin (no path, query, userinfo or
    trailing slash: the signature names origin + request target), signer_env_file an absolute path."""
    if not isinstance(api, dict) or set(api) != PEOPLE_API_KEYS:
        raise GroupSyncError("config people_api must be {base_url, signer_env_file}")
    base = api["base_url"]
    parts = urllib.parse.urlsplit(base) if isinstance(base, str) else None
    # "https://" + netloc is the whole string: that alone rules out http, a path, a query, a fragment and a trailing slash.
    try:
        port = parts.port if parts is not None else None
    except ValueError:  # not a number, or outside 0-65535
        port = -1
    if (parts is None or not parts.hostname or parts.username is not None or parts.password is not None
            or base != f"https://{parts.netloc}" or parts.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535)):
        raise GroupSyncError("config people_api.base_url must be exactly https://host[:port]")
    _absolute(api["signer_env_file"], "people_api.signer_env_file")


def _check_reaction_map(mapping: Any) -> None:
    ok = isinstance(mapping, dict) and all(
        isinstance(k, str) and 0 < len(k) <= EMOJI_KEY_MAX and _reaction_emoji(k)
        and isinstance(v, str) and EMOJI_TYPE_RE.fullmatch(v)
        for k, v in mapping.items())
    if not ok:
        raise GroupSyncError("config reaction_map must be {emoji: Feishu emoji_type} (letters, digits, underscore)")


def _check_sender_allowlist(listed: Any) -> None:
    ok = (isinstance(listed, list) and listed and all(isinstance(k, str) and HEX64_RE.fullmatch(k) for k in listed)
          and len(set(listed)) <= MAX_SENDER_ALLOWLIST)
    if not ok:  # never say what was in it
        raise GroupSyncError(f"config feishu_sender_allowlist must be a non-empty list of at most {MAX_SENDER_ALLOWLIST} "
                             "distinct Buzz pubkeys (64 lowercase hex characters each)")


def _check_unmapped_senders(cfg: Mapping[str, Any]) -> None:
    mode = cfg["feishu_unmapped_senders"]
    if not isinstance(mode, str) or mode not in UNMAPPED_SENDER_MODES:  # never say what was in it
        raise GroupSyncError('config feishu_unmapped_senders must be "context" (the default) or "skip"')
    if mode == "context" and "feishu_sender_allowlist" in cfg:
        # The list exists to keep everyone else's words out of Buzz; letting strangers in as context contradicts it.
        raise GroupSyncError('config feishu_unmapped_senders "context" cannot be combined with feishu_sender_allowlist '
                             "(the list keeps everyone else's words out of Buzz)")


def _check_buzz_unmapped_senders(cfg: Mapping[str, Any]) -> None:
    mode = cfg["buzz_unmapped_senders"]
    if not isinstance(mode, str) or mode not in BUZZ_UNMAPPED_SENDER_MODES:  # never say what was in it
        raise GroupSyncError('config buzz_unmapped_senders must be "skip" (the default) or "context"')


def _check_buzz_unmanaged_agents(cfg: Mapping[str, Any]) -> None:
    mode = cfg["buzz_unmanaged_agents"]
    if not isinstance(mode, str) or mode not in BUZZ_UNMANAGED_AGENT_MODES:  # never say what was in it
        raise GroupSyncError('config buzz_unmanaged_agents must be "relay" (the default) or "skip"')


def _validated_config(cfg: Any) -> dict[str, Any]:
    if not isinstance(cfg, dict):
        raise GroupSyncError("config must be a JSON object")
    unknown, missing = set(cfg) - CONFIG_KEYS - OPTIONAL_CONFIG_KEYS, CONFIG_KEYS - set(cfg)
    if unknown or missing:
        raise GroupSyncError(f"config keys mismatch: unknown {sorted(unknown)}, missing {sorted(missing)}")
    checks = [
        ("channel_id", UUID_RE), ("owner_open_id", OPEN_ID_RE), ("owner_app_id", APP_ID_RE), ("mirror_pubkey", HEX64_RE),
    ]
    for key, pattern in checks:
        if not isinstance(cfg[key], str) or not pattern.fullmatch(cfg[key]):
            raise GroupSyncError(f"config {key} is malformed")
    if cfg["chat_id"] is not None and (not isinstance(cfg["chat_id"], str) or not CHAT_ID_RE.fullmatch(cfg["chat_id"])):
        raise GroupSyncError("config chat_id must be null or an oc_ id")
    for key in ("mirror_env_file", "buzz_cli", "lark_cli"):
        _absolute(cfg[key], key)
    _check_people_api(cfg["people_api"])
    try:
        # Never the ~/.local/bin/buzz wrapper: it loads the owner's key.
        sync.validate_buzz_cli_path(cfg["buzz_cli"], cfg["buzz_cli_sha256"])
    except sync.SyncError as exc:
        raise GroupSyncError(f"config buzz_cli: {exc}") from None
    if Path(cfg["lark_cli"]).name != "lark-cli":
        raise GroupSyncError("config lark_cli must point at lark-cli")
    if not isinstance(cfg["remove_extras"], bool):
        raise GroupSyncError("config remove_extras must be true or false")
    if "reaction_map" in cfg:
        _check_reaction_map(cfg["reaction_map"])
    if "feishu_sender_allowlist" in cfg:
        _check_sender_allowlist(cfg["feishu_sender_allowlist"])
    if "feishu_unmapped_senders" in cfg:
        _check_unmapped_senders(cfg)
    if "buzz_unmapped_senders" in cfg:
        _check_buzz_unmapped_senders(cfg)
    if "buzz_unmanaged_agents" in cfg:
        _check_buzz_unmanaged_agents(cfg)
    if "membership_sync" in cfg and (not isinstance(cfg["membership_sync"], str) or cfg["membership_sync"] not in MEMBERSHIP_SYNC_MODES):
        raise GroupSyncError('config membership_sync must be "two_way" (the default) or "buzz_to_feishu"')
    if "reaction_sync" in cfg and (not isinstance(cfg["reaction_sync"], str) or cfg["reaction_sync"] not in REACTION_SYNC_MODES):
        raise GroupSyncError('config reaction_sync must be "two_way" (the default) or "agents_only"')
    if "people_cache_file" in cfg:
        _absolute(cfg["people_cache_file"], "people_cache_file")
    if "identity" in cfg and cfg["identity"] not in IDENTITY_MODES:
        raise GroupSyncError('config identity must be "union_id" (the default) or "email"')
    if "message_format" in cfg and (not isinstance(cfg["message_format"], str) or cfg["message_format"] not in MESSAGE_FORMATS):
        raise GroupSyncError('config message_format must be "card" (the default) or "text"')
    if not isinstance(cfg["agents"], dict):
        raise GroupSyncError("config agents must be an object")
    seen: dict[str, set[str]] = {key: set() for key in AGENT_KEYS}
    for pubkey, agent in cfg["agents"].items():
        if not HEX64_RE.fullmatch(str(pubkey)) or not isinstance(agent, dict) or set(agent) != AGENT_KEYS:
            raise GroupSyncError("config agents entries must be <pubkey hex>: {app_id, lark_config_dir, lark_data_dir}")
        if not isinstance(agent["app_id"], str) or not APP_ID_RE.fullmatch(agent["app_id"]):
            raise GroupSyncError("config agent app_id is malformed")
        if agent["app_id"] == cfg["owner_app_id"]:
            raise GroupSyncError("config agent app cannot be the owner app")
        _absolute(agent["lark_config_dir"], "agent lark_config_dir")
        _absolute(agent["lark_data_dir"], "agent lark_data_dir")
        for key in AGENT_KEYS:
            # One app and one profile per agent: a shared one would speak for two agents.
            if agent[key] in seen[key]:
                raise GroupSyncError(f"config agents share a {key}; every agent needs its own")
            seen[key].add(agent[key])
    desk = cfg["desk_pubkey"]
    if not isinstance(desk, str) or not HEX64_RE.fullmatch(desk) or desk not in cfg["agents"]:
        raise GroupSyncError("config desk_pubkey must identify one configured Desk agent")
    if cfg["agents"][desk]["app_id"] == cfg["owner_app_id"]:
        raise GroupSyncError("config Desk must use its own Feishu app")
    return cfg


def load_config(path: Path) -> dict[str, Any]:
    return _validated_config(_load_json(_read_owner_only(Path(path), "config"), "config"))


def _write_config(path: Path, cfg: Mapping[str, Any]) -> None:
    _write_owner_only(Path(path), json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


def migrate_desk_config(path: Path, desk_pubkey: str, *, apply: bool = False,
                        now: datetime | None = None) -> dict[str, Any]:
    """Validate a pre-Desk binding; on explicit apply, keep a private backup and atomically add its Desk."""
    path = Path(path)
    original = _read_owner_only(path, "config")
    old = _load_json(original, "config")
    if not isinstance(old, dict):
        raise GroupSyncError("config must be a JSON object")
    if "desk_pubkey" in old:
        if old["desk_pubkey"] != desk_pubkey:
            raise GroupSyncError("config already designates a different Desk")
        _validated_config(old)
        return {"status": "already_migrated", "channel_id": old["channel_id"], "applied": False}
    candidate = _validated_config({**old, "desk_pubkey": desk_pubkey})
    result = {"status": "ready", "channel_id": candidate["channel_id"], "applied": False}
    if not apply:
        return result
    # Timers must be stopped before this command. A second read catches accidental intervening edits.
    if _read_owner_only(path, "config") != original:
        raise GroupSyncError("config changed during Desk migration")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak.desk-{stamp}")
    if backup.exists() or backup.is_symlink():
        raise GroupSyncError("Desk migration backup path already exists")
    _write_owner_only(backup, original)
    _write_config(path, candidate)
    return {"status": "migrated", "channel_id": candidate["channel_id"], "applied": True,
            "backup": str(backup)}


# ---------------------------------------------------------------- CLI adapters


def _parse_output(result: subprocess.CompletedProcess) -> Any:
    for stream in (result.stdout, result.stderr):
        try:
            return json.loads(stream or "")
        except json.JSONDecodeError:
            continue
    return None


class LarkCli:
    def __init__(self, cli: str, env: Mapping[str, str], *, runner: Any = subprocess.run) -> None:
        self.cli = cli
        self.env = dict(env)
        self.runner = runner

    def _run(self, what: str, args: list[str], cwd: Path | None = None) -> tuple[subprocess.CompletedProcess, Any]:
        extra = {} if cwd is None else {"cwd": str(cwd)}  # lark-cli takes local files only relative to its cwd
        try:
            result = self.runner([self.cli, *args], capture_output=True, text=True, timeout=90, check=False, env=self.env,
                                 **extra)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CliError(what, -1, type(exc).__name__, definite=False) from None
        return result, _parse_output(result)

    def call(self, what: str, args: list[str], *, full: bool = False, cwd: Path | None = None) -> dict[str, Any]:
        result, payload = self._run(what, [*args, "--format", "json"], cwd)
        if not isinstance(payload, dict):
            raise CliError(what, result.returncode or -1, "non-JSON output", definite=False)
        if payload.get("ok") is not True or result.returncode != 0:
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            code = err.get("code") if isinstance(err.get("code"), int) else (result.returncode or -1)
            # Never echo argv or the server message: both can carry an email.
            raise CliError(what, code, str(err.get("subtype") or err.get("type") or ""),
                           definite=str(err.get("type") or "") in LARK_DEFINITE_ERROR_TYPES,
                           error_type=str(err.get("type") or ""))
        if full:
            return payload
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _page(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """Rows, and whether --page-all stopped at --page-limit with more left (either signal)."""
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        pagination = meta.get("pagination") if isinstance(meta.get("pagination"), dict) else {}
        more = bool(data.get("has_more")) or pagination.get("complete") is False
        return [m for m in data.get("messages") or [] if isinstance(m, dict)], more

    def identity(self) -> tuple[str, str]:
        """(app id, user open_id) of this profile, from `auth status`."""
        result, payload = self._run("auth status", ["auth", "status"])
        if not isinstance(payload, dict):
            raise CliError("auth status", result.returncode or -1, "non-JSON output", definite=False)
        user = ((payload.get("identities") or {}).get("user") or {}) if isinstance(payload.get("identities"), dict) else {}
        return str(payload.get("appId") or ""), str(user.get("openId") or "")

    def chat(self, chat_id: str, user_id_type: str = "open_id") -> dict[str, Any]:
        return self.call("chat get", ["api", "GET", f"/open-apis/im/v1/chats/{chat_id}",
                                      "--params", json.dumps({"user_id_type": user_id_type}), "--as", "user"])

    def user_info(self) -> tuple[str, str]:
        """(open_id, union_id) of the logged-in user, from the user's own token."""
        data = self.call("owner user info", ["api", "GET", "/open-apis/authen/v1/user_info", "--as", "user"])
        return str(data.get("open_id") or ""), str(data.get("union_id") or "")

    def _member_page(self, chat_id: str, id_type: str, kind: str) -> dict[str, Any]:
        return self.call("chat members", ["im", "+chat-members-list", "--chat-id", chat_id, "--as", "user", "--page-all",
                                          "--member-id-type", id_type, "--member-types", kind])

    def members(self, chat_id: str, user_id_type: str = "open_id") -> tuple[set[str], dict[str, str]]:
        """(users as `user_id_type` ids, {app id: the bot's member id (an open_id)}). A person's id can be asked for
        as union_id; a bot is always listed by its open_id, the form its @ mentions use."""
        listing = self.member_listing(chat_id, user_id_type)
        return set(listing.users), listing.bots

    @staticmethod
    def _listing_complete(data: Mapping[str, Any], kinds: tuple[str, ...]) -> bool:
        """Every page was read (seen live: `has_more`, `truncations`, and `user_total` / `bot_total` next to the lists)."""
        if data.get("has_more") is not False:
            return False
        truncations = data.get("truncations")
        if not isinstance(truncations, list) or truncations:
            return False
        for kind in kinds:
            total = data.get(f"{kind}_total")
            rows = data.get(f"{kind}s")
            if not isinstance(rows, list) or type(total) is not int or total != len(rows):
                return False
        return True

    def member_listing(self, chat_id: str, user_id_type: str = "open_id") -> "MemberListing":
        if user_id_type == "open_id":
            users_data = bots_data = self.call("chat members", ["im", "+chat-members-list", "--chat-id", chat_id,
                                                                "--as", "user", "--page-all"])
            complete = self._listing_complete(users_data, ("user", "bot"))
        else:
            users_data = self._member_page(chat_id, user_id_type, "user")
            bots_data = self._member_page(chat_id, "open_id", "bot")
            complete = self._listing_complete(users_data, ("user",)) and self._listing_complete(bots_data, ("bot",))
        users = {str(u.get("member_id")): str(u.get("name") or "") for u in users_data.get("users") or []
                 if isinstance(u, dict) and u.get("member_id")}
        bots = {str(b.get("app_id")): str(b.get("member_id")) for b in bots_data.get("bots") or []
                if isinstance(b, dict) and b.get("app_id") and b.get("member_id")}
        return MemberListing(users, bots, complete)

    def search_user(self, email: str) -> list[dict[str, Any]]:
        # Never echoes argv or the server's answer (both carry the address): see `call`.
        data = self.call("user search", ["contact", "+search-user", "--as", "user", "--query", email,
                                         "--exclude-external-users"])
        return [u for u in data.get("users") or [] if isinstance(u, dict)]

    def message_view(self, message_id: str, user_id_type: str) -> dict[str, Any] | None:
        """One message as Feishu shows it with ids of `user_id_type` (the list API ignores that parameter): the
        sender and every mention come back in that id kind, with the same mentions[].key as the list's row."""
        if not MESSAGE_ID_RE.fullmatch(message_id):
            return None
        try:
            data = self.call("message get", ["api", "GET", f"/open-apis/im/v1/messages/{message_id}",
                                             "--params", json.dumps({"user_id_type": user_id_type}), "--as", "user"])
        except CliError as exc:
            if exc.kind == "not_found":  # deleted in the meantime: there is nothing to pair
                return None
            raise
        items = data.get("items")
        return items[0] if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) else None

    def change_members(self, method: str, chat_id: str, ids: list[str], id_type: str) -> int:
        """Returns how many ids Feishu reported as not applied."""
        params: dict[str, Any] = {"member_id_type": id_type}
        if method == "POST":
            params["succeed_type"] = 1  # add the usable ids and list the rest; 0 fails the whole call
        data = self.call(f"chat members {method.lower()}", ["api", method, f"/open-apis/im/v1/chats/{chat_id}/members",
                                                            "--params", json.dumps(params),
                                                            "--data", json.dumps({"id_list": ids}), "--as", "user"])
        return sum(len(data.get(key) or []) for key in MEMBER_FAILURE_LISTS)

    def messages(self, chat_id: str, start: datetime, *, order: str = "asc",
                 page_limit: int = FEISHU_PAGE_LIMIT) -> tuple[list[dict[str, Any]], bool]:
        """Messages since `start`, and whether --page-all stopped at --page-limit with more left."""
        return self._page(self.call("chat messages", ["im", "+chat-messages-list", "--as", "user", "--chat-id", chat_id,
                                                      "--order", order, "--no-reactions", "--page-all",
                                                      "--page-limit", str(page_limit),
                                                      "--start", start.astimezone(timezone.utc).isoformat()], full=True))

    def thread_messages(self, root_message_id: str) -> tuple[list[dict[str, Any]], bool]:
        """The newest replies of a thread (newest first), and whether older ones were left unread."""
        try:
            payload = self.call("thread messages", ["im", "+threads-messages-list", "--as", "user",
                                                    "--thread", root_message_id, "--order", "desc", "--no-reactions",
                                                    "--page-all", "--page-limit", str(THREAD_PAGE_LIMIT)], full=True)
        except CliError as exc:
            if exc.kind == "not_found":  # the message has no thread (yet)
                return [], False
            raise
        return self._page(payload)

    def _message_id(self, data: Mapping[str, Any], what: str) -> str:
        message_id = data.get("message_id")
        if not isinstance(message_id, str) or not MESSAGE_ID_RE.fullmatch(message_id):
            raise CliError(what, -1, "no message id", definite=False)
        return message_id

    def send(self, chat_id: str, text: str, key: str) -> str:
        data = self.call("send", ["im", "+messages-send", "--as", "bot", "--chat-id", chat_id, "--text", text,
                                  "--idempotency-key", key])
        return self._message_id(data, "send")

    def reply(self, message_id: str, text: str, key: str) -> str:
        data = self.call("reply", ["im", "+messages-reply", "--as", "bot", "--message-id", message_id, "--text", text,
                                   "--reply-in-thread", "--idempotency-key", key])
        return self._message_id(data, "reply")

    def send_card(self, chat_id: str, card: str, key: str) -> str:
        """An interactive message: `card` is the card's JSON, sent as it is."""
        data = self.call("send card", ["im", "+messages-send", "--as", "bot", "--chat-id", chat_id, "--msg-type", "interactive",
                                       "--content", card, "--idempotency-key", key])
        return self._message_id(data, "send card")

    def reply_card(self, message_id: str, card: str, key: str) -> str:
        data = self.call("reply card", ["im", "+messages-reply", "--as", "bot", "--message-id", message_id, "--msg-type",
                                        "interactive", "--content", card, "--reply-in-thread", "--idempotency-key", key])
        return self._message_id(data, "reply card")

    def update_card(self, message_id: str, card: str) -> None:
        """Replace an interactive message in place. Feishu requires the same app identity that sent it and
        config.update_multi=true in both the existing and replacement cards."""
        self.call("update card", ["api", "PATCH", f"/open-apis/im/v1/messages/{message_id}",
                                  "--data", json.dumps({"content": card}), "--as", "bot"])

    def update_text(self, message_id: str, text: str) -> None:
        """Replace a text message in place through the ordinary message edit endpoint."""
        content = json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":"))
        self.call("update text", ["api", "PUT", f"/open-apis/im/v1/messages/{message_id}",
                                  "--data", json.dumps({"msg_type": "text", "content": content}, ensure_ascii=False),
                                  "--as", "bot"])

    def download_image(self, message_id: str, key: str, workdir: Path) -> Path:
        """A message's image into `workdir`, an empty directory of ours that is also the call's working directory (lark-cli takes
        output paths only relative to it). lark-cli adds an extension of its own choosing, and what it prints about where it saved
        the file is not used: the one file that ended up in `workdir` is the download."""
        if not MESSAGE_ID_RE.fullmatch(message_id) or not FEISHU_IMAGE_KEY_RE.fullmatch(key):
            raise CliError("resource download", 1, "bad id", definite=True)
        self.call("resource download", ["im", "+messages-resources-download", "--as", "user", "--message-id", message_id,
                                        "--file-key", key, "--type", "image", "--output", "download"], cwd=workdir)
        found = list(workdir.iterdir())
        if len(found) != 1:
            raise CliError("resource download", 0, "no single file", definite=False)
        return found[0]

    def send_image(self, chat_id: str, name: str, key: str, *, cwd: Path) -> str:
        """`name` is a file directly in `cwd`: lark-cli refuses absolute paths and `..`, and uploads the file itself."""
        data = self.call("send image", ["im", "+messages-send", "--as", "bot", "--chat-id", chat_id, "--image", name,
                                        "--idempotency-key", key], cwd=cwd)
        return self._message_id(data, "send image")

    def reply_image(self, message_id: str, name: str, key: str, *, cwd: Path) -> str:
        data = self.call("reply image", ["im", "+messages-reply", "--as", "bot", "--message-id", message_id, "--image", name,
                                         "--reply-in-thread", "--idempotency-key", key], cwd=cwd)
        return self._message_id(data, "reply image")

    def react(self, message_id: str, emoji_type: str) -> str:
        """Adding the same emoji twice returns the same reaction (Feishu dedupes per message, emoji and
        caller), so a retry after an unknown outcome cannot double it."""
        data = self.call("react", ["im", "reactions", "create", "--as", "bot",
                                   "--params", json.dumps({"message_id": message_id}),
                                   "--data", json.dumps({"reaction_type": {"emoji_type": emoji_type}})])
        reaction_id = data.get("reaction_id")
        if not isinstance(reaction_id, str) or not reaction_id:
            raise CliError("react", -1, "no reaction id", definite=False)
        return reaction_id

    def unreact(self, message_id: str, reaction_id: str) -> None:
        self.call("unreact", ["im", "reactions", "delete", "--as", "bot",
                              "--params", json.dumps({"message_id": message_id, "reaction_id": reaction_id})])

    def reaction_details(self, message_ids: list[str], user_id_type: str) -> dict[str, list[tuple[str, str, str]]]:
        """{message id: [(operator_type, operator_id, emoji_type)]} for the messages whose reactions could be read whole: one
        `im reactions batch_query` (the user's token; at most 10 per message, the API's limit), then the next pages of a
        message that has more. A message Feishu could not answer for, or that still has pages left, is not in the result."""
        out: dict[str, list[tuple[str, str, str]]] = {}
        queries: list[dict[str, str]] = [{"message_id": m} for m in message_ids]
        for _ in range(1 + REACTION_PAGES_MAX):
            if not queries:
                break
            data = self.call("reactions query", ["im", "reactions", "batch_query", "--as", "user",
                                                 "--params", json.dumps({"user_id_type": user_id_type}),
                                                 "--data", json.dumps({"queries": queries, "page_size_per_message": 10})])
            failed = {str(f.get("message_id")) for f in data.get("fail_msg_reaction_details") or [] if isinstance(f, dict)}
            queries = []
            for row in data.get("success_msg_reaction_details") or []:
                message_id = str(row.get("message_id") or "") if isinstance(row, dict) else ""
                if not message_id or message_id in failed:
                    continue
                items = out.setdefault(message_id, [])
                for item in row.get("message_reaction_items") or []:
                    operator = item.get("operator") if isinstance(item, dict) and isinstance(item.get("operator"), dict) else {}
                    items.append((str(operator.get("operator_type") or ""), str(operator.get("operator_id") or ""),
                                  str(item.get("emoji_type") or "") if isinstance(item, dict) else ""))
                if row.get("has_more"):
                    if row.get("page_token"):
                        queries.append({"message_id": message_id, "page_token": str(row["page_token"])})
                    else:
                        failed.add(message_id)
            for message_id in failed:
                out.pop(message_id, None)
        for query in queries:  # pages left over: this message cannot be judged this round
            out.pop(query["message_id"], None)
        return out

    def create_chat(self, name: str, owner_open_id: str) -> str:
        data = self.call("chat create", ["im", "+chat-create", "--as", "bot", "--name", name, "--owner", owner_open_id,
                                         "--set-bot-manager", "--type", "private"])
        chat_id = data.get("chat_id")
        if not isinstance(chat_id, str) or not CHAT_ID_RE.fullmatch(chat_id):
            raise GroupSyncError("chat create returned no chat id")
        return chat_id

    def user_in_scope(self, open_id: str) -> bool:
        try:
            self.call("contact lookup", ["api", "GET", f"/open-apis/contact/v3/users/{open_id}",
                                         "--params", json.dumps({"user_id_type": "open_id"}), "--as", "bot"])
        except CliError as exc:
            if exc.code == 41050:  # no user authority: outside the app's availability
                return False
            raise
        return True

    def bot_scopes(self) -> set[str]:
        data = self.call("scopes", ["api", "GET", "/open-apis/application/v6/scopes", "--as", "bot"])
        return {str(s.get("scope_name")) for s in data.get("scopes") or []
                if isinstance(s, dict) and s.get("grant_status") == 1}


class BuzzCli:
    DEFINITE_EXIT_CODES = frozenset({1, 3})  # bad input, auth error: nothing reached the relay

    def __init__(self, cli: str, env: Mapping[str, str], *, runner: Any = subprocess.run) -> None:
        self.cli = cli
        self.env = dict(env)
        self.runner = runner

    def call(self, what: str, args: list[str], stdin: str | None = None) -> Any:
        try:
            result = self.runner([self.cli, *args], input=stdin, capture_output=True, text=True,
                                 timeout=60, check=False, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CliError(what, -1, type(exc).__name__, definite=False) from None
        if result.returncode != 0:
            raise CliError(what, result.returncode, definite=result.returncode in self.DEFINITE_EXIT_CODES)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise CliError(what, 0, "non-JSON output", definite=False) from None

    def whoami(self) -> str:
        value = self.call("whoami", ["users", "get"])
        rows = value if isinstance(value, list) else [value]
        pubkeys = {str(r.get("pubkey")) for r in rows if isinstance(r, dict) and r.get("pubkey")}
        return pubkeys.pop() if len(pubkeys) == 1 else ""

    def members(self, channel: str) -> list[dict[str, Any]]:
        value = self.call("channel members", ["channels", "members", "--channel", channel])
        if not isinstance(value, list):
            raise GroupSyncError("Buzz channel members is not a list")
        return [r for r in value if isinstance(r, dict) and HEX64_RE.fullmatch(str(r.get("pubkey") or ""))]

    def names(self, pubkeys: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for group in batches(sorted(set(pubkeys)), PROFILE_BATCH):
            args = ["users", "get"]
            for pubkey in group:
                args += ["--pubkey", pubkey]
            value = self.call("profiles", args)
            for row in value if isinstance(value, list) else []:
                if isinstance(row, dict) and row.get("pubkey") and row.get("display_name"):
                    out[str(row["pubkey"])] = str(row["display_name"])
        return out

    def channel_name(self, channel: str) -> str:
        """The channel's name (on a card's byline and summary); "" when the answer has none. A failing call raises CliError."""
        value = self.call("channel get", ["channels", "get", "--channel", channel])
        name = value.get("name") if isinstance(value, dict) else None
        return name if isinstance(name, str) else ""

    def messages(self, channel: str, since: int, kinds: str = MIRROR_KINDS) -> list[dict[str, Any]]:
        """Every mirrorable event since `since`, paged backwards (the relay returns the newest first)."""
        found: dict[str, dict[str, Any]] = {}
        before: int | None = None
        for _ in range(BUZZ_PAGE_MAX):
            args = ["messages", "get", "--channel", channel, "--kinds", kinds, "--since", str(since),
                    "--limit", str(BUZZ_PAGE_LIMIT)]
            if before is not None:
                args += ["--before", str(before)]
            page = self.call("messages", args)
            if not isinstance(page, list):
                raise GroupSyncError("Buzz messages get did not return a list")
            if not all(isinstance(e, dict) and HEX64_RE.fullmatch(str(e.get("id") or "")) for e in page):
                # Dropping a row would shorten the page and end paging early: refuse instead.
                raise GroupSyncError("Buzz messages get returned a malformed event")
            fresh = [e for e in page if str(e["id"]) not in found]
            for e in fresh:
                found[str(e["id"])] = e
            if len(page) < BUZZ_PAGE_LIMIT:
                return list(found.values())
            if not fresh:  # --before is inclusive: one second holds more than a page
                raise GroupSyncError("Buzz channel has more same-second messages than one page holds")
            before = min(int(e.get("created_at") or 0) for e in page)
        raise BuzzBacklogError("Buzz backlog exceeds the page limit; refusing to skip messages "
                               "(run one round with --skip-backlog to drop it on purpose)")

    def thread_root(self, channel: str, event_id: str, *, expect: str | None = None) -> dict[str, Any]:
        """The top-level message of the thread that holds `event_id` (a root, a reply or a nested reply). buzz 0.5.23
        `messages thread` returns the whole thread as a JSON array of events, the root (no e tag) first; `--depth-limit 0`
        keeps only the root, which is all a mirror needs. Exactly one top-level event must come back, and the one the
        caller expects when it already knows the root's id: anything else is refused, never guessed."""
        value = self.call("thread", ["messages", "thread", "--channel", channel, "--event", event_id, "--depth-limit", "0"])
        if not isinstance(value, list) or not all(isinstance(e, dict) and HEX64_RE.fullmatch(str(e.get("id") or "")) for e in value):
            raise GroupSyncError("Buzz messages thread did not return a list of events")
        roots = [e for e in value if _buzz_parent(e) is None]
        if len(roots) != 1:
            raise GroupSyncError("Buzz messages thread did not return exactly one thread root")
        if expect is not None and roots[0]["id"] != expect:
            raise GroupSyncError("Buzz messages thread returned another root than the one asked for")
        return roots[0]

    def thread_event(self, channel: str, event_id: str) -> dict[str, Any]:
        """Read the containing thread and return the exact event requested. This is used by edit overlays, whose target
        may be older than the ordinary message overlap window; no neighbouring event is ever guessed."""
        value = self.call("thread", ["messages", "thread", "--channel", channel, "--event", event_id])
        if not isinstance(value, list) or not all(isinstance(e, dict) and HEX64_RE.fullmatch(str(e.get("id") or ""))
                                                  for e in value):
            raise GroupSyncError("Buzz messages thread did not return a list of events")
        found = [e for e in value if str(e.get("id") or "") == event_id]
        if len(found) != 1:
            raise GroupSyncError("Buzz messages thread did not return the requested event")
        return found[0]

    def download_media(self, segment: str, dest: Path) -> None:
        """Relay media by its `<sha256>[.ext]` path segment (never a url, so the CLI only ever talks to its own relay), signed
        with this identity, into `dest`."""
        if not MEDIA_SEGMENT_RE.fullmatch(segment):
            raise CliError("media get", 1, "bad media path", definite=True)
        try:
            result = self.runner([self.cli, "media", "get", segment, "-o", str(dest)], input=None, capture_output=True,
                                 text=True, timeout=120, check=False, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CliError("media get", -1, type(exc).__name__, definite=False) from None
        if result.returncode != 0:
            raise CliError("media get", result.returncode, definite=result.returncode in self.DEFINITE_EXIT_CODES)

    def send(self, channel: str, content: str, *, reply_to: str | None = None, mentions: tuple[str, ...] = (),
             files: tuple[str, ...] = ()) -> str:
        args = ["messages", "send", "--channel", channel, "--content", "-"]
        if reply_to:
            args += ["--reply-to", reply_to]
        for pubkey in mentions:
            args += ["--mention", pubkey]
        for path in files:  # uploaded and attached as imeta by the CLI
            args += ["--file", path]
        value = self.call("send", args, stdin=content)
        if isinstance(value, dict) and value.get("accepted") is False:
            raise CliError("send", 0, "rejected", definite=True)
        event_id = value.get("event_id") if isinstance(value, dict) else None
        if not isinstance(event_id, str) or not HEX64_RE.fullmatch(event_id):
            raise CliError("send", 0, "no event id", definite=False)
        return event_id


# ---------------------------------------------------------------- commands


@dataclass
class Clients:
    owner: LarkCli
    agents: dict[str, LarkCli]  # app_id -> that agent's own profile
    buzz: BuzzCli
    http: Any = _http_get  # the people endpoint's and the relay query's transport; tests pass their own
    relay_url: str = ""  # the mirror env's BUZZ_RELAY_URL: where the agent directory is read (ADR-0019)
    mirror_key: str = ""  # the mirror env's BUZZ_PRIVATE_KEY: signs the reactions it carries into Buzz, in this process (ADR-0020)
    mirror_auth_tag: str = ""  # the mirror env's BUZZ_AUTH_TAG: an agent identity is a relay member only with it


def _clients(cfg: Mapping[str, Any], base_env: Mapping[str, str], runner: Any, http: Any = None) -> Clients:
    mirror = read_env_file(Path(cfg["mirror_env_file"]))
    if not mirror.get("BUZZ_PRIVATE_KEY"):
        raise GroupSyncError("mirror env file has no BUZZ_PRIVATE_KEY")
    agents = {
        agent["app_id"]: LarkCli(cfg["lark_cli"], child_env(base_env, {
            **LARK_ENV, "LARKSUITE_CLI_CONFIG_DIR": agent["lark_config_dir"],
            "LARKSUITE_CLI_DATA_DIR": agent["lark_data_dir"]}), runner=runner)
        for agent in cfg["agents"].values()
    }
    return Clients(
        owner=LarkCli(cfg["lark_cli"], child_env(base_env, LARK_ENV), runner=runner),
        agents=agents,
        buzz=BuzzCli(cfg["buzz_cli"], child_env(base_env, mirror), runner=runner),
        http=http or _http_get,
        relay_url=mirror.get("BUZZ_RELAY_URL", ""),
        mirror_key=mirror.get("BUZZ_PRIVATE_KEY", ""),
        mirror_auth_tag=mirror.get("BUZZ_AUTH_TAG", ""),
    )


def _owner_profile_ok(cfg: Mapping[str, Any], owner: LarkCli) -> bool:
    try:
        return owner.identity() == (cfg["owner_app_id"], cfg["owner_open_id"])
    except CliError:
        return False


def _desired_agent_bots(cfg: Mapping[str, Any], members: list[dict[str, Any]]) -> set[str]:
    return {cfg["agents"][m["pubkey"]]["app_id"] for m in members
            if m.get("role") == "bot" and m["pubkey"] in cfg["agents"]}


def preflight_command(config_path: Path, mode: str, chat_id: str | None, *, base_env: Mapping[str, str],
                      runner: Any = subprocess.run) -> dict[str, Any]:
    cfg = load_config(config_path)
    clients = _clients(cfg, base_env, runner)
    if mode == "new":
        result = preflight_new_chat(owner_in_scope=clients.owner.user_in_scope(cfg["owner_open_id"]),
                                    bot_scopes=clients.owner.bot_scopes(),
                                    profile_ok=_owner_profile_ok(cfg, clients.owner))
    elif mode == "existing":
        if not chat_id or not CHAT_ID_RE.fullmatch(chat_id):
            raise GroupSyncError("preflight --mode existing needs --chat-id oc_...")
        if not _owner_profile_ok(cfg, clients.owner):
            # Everything below is read as the logged-in user: it must be the configured owner.
            return {"mode": mode, "ok": False, "problems": ["owner_profile_mismatch"], "warnings": [],
                    "can_remove": False}
        try:
            chat = clients.owner.chat(chat_id)
        except CliError as exc:
            # Not in the chat, a wrong chat_id or an expired login all look alike here.
            return {"mode": mode, "ok": False, "problems": ["chat_unreadable"], "warnings": [], "can_remove": False,
                    "error_code": exc.code}
        _, actual_bots = clients.owner.members(chat_id)
        wanted = _desired_agent_bots(cfg, clients.buzz.members(cfg["channel_id"])) | {cfg["owner_app_id"]}
        result = preflight_existing_chat(chat, owner_open_id=cfg["owner_open_id"],
                                         bots_to_add=len(wanted - set(actual_bots)),
                                         remove_extras=cfg["remove_extras"])
    else:
        raise GroupSyncError("preflight --mode must be new or existing")
    return {"mode": mode, "ok": result.ok, "problems": list(result.problems), "warnings": list(result.warnings),
            "can_remove": result.can_remove}


def create_chat_command(config_path: Path, name: str, *, base_env: Mapping[str, str],
                        runner: Any = subprocess.run) -> dict[str, Any]:
    cfg = load_config(config_path)
    if cfg["chat_id"]:
        raise GroupSyncError("config is already bound to a chat; refusing to create another")
    intent = Path(config_path).with_name(Path(config_path).name + CREATE_INTENT_SUFFIX)
    if intent.exists() or intent.is_symlink():
        raise GroupSyncError(f"an earlier create-chat may already have created the group ({intent.name} remains): "
                             "find it with `lark-cli im +chat-search --as user`, run bind with its chat_id, "
                             "then delete the intent file")
    report = preflight_command(config_path, "new", None, base_env=base_env, runner=runner)
    if not report["ok"]:
        raise GroupSyncError(f"preflight failed: {', '.join(report['problems'])}")
    # Recorded before the call: if the chat is created but the config is not rewritten,
    # a second create-chat must not create another group.
    _write_owner_only(intent, json.dumps({"name": name}, ensure_ascii=False))
    try:
        chat_id = _clients(cfg, base_env, runner).owner.create_chat(name, cfg["owner_open_id"])
    except CliError as exc:
        if exc.definite:  # refused: no group exists
            intent.unlink()
        raise
    _write_config(config_path, dict(cfg, chat_id=chat_id))
    intent.unlink()
    return {"chat_id": chat_id, "warnings": report["warnings"]}


def bind_command(config_path: Path, chat_id: str, *, base_env: Mapping[str, str],
                 runner: Any = subprocess.run) -> dict[str, Any]:
    cfg = load_config(config_path)
    if cfg["chat_id"] and cfg["chat_id"] != chat_id:
        raise GroupSyncError("config is already bound to another chat")
    report = preflight_command(config_path, "existing", chat_id, base_env=base_env, runner=runner)
    if not report["ok"]:
        raise GroupSyncError(f"preflight failed: {', '.join(report['problems'])}")
    _write_config(config_path, dict(cfg, chat_id=chat_id))
    return {"chat_id": chat_id, "warnings": report["warnings"]}


def _new_report() -> dict[str, Any]:
    return {"added_users": 0, "removed_users": 0, "added_bots": 0, "removed_bots": 0, "blocked_bots": 0,
            "member_failures": 0, "removals_withheld": None, "unmapped_members": 0, "identity_conflicts": 0,
            "backlog_skipped": [],
            "to_feishu": 0, "to_buzz": 0, "messages_updated": 0,
            "unknown": 0, "failed": 0, "errors": 0, "skipped": {},
            # Reactions are decoration: their failures are counted, but never make a round need attention.
            "reactions_added": 0, "reactions_removed": 0, "reactions_failed": 0,
            "thread_roots_backfilled": 0, "thread_root_unavailable": 0, "thread_root_failed": 0, "thread_roots_deferred": 0,
            # Cards are how a message is said, not something to look at: a card Feishu refused and that went out as text
            # instead is only counted.
            "cards_sent": 0, "cards_fallback_text": 0, "relayed_agents": 0,
            # The agent directory (ADR-0019) is a convenience: a failed lookup or a contested claim is counted, never alarmed on.
            "directory_agents": 0, "directory_failed": 0, "directory_conflicts": 0,
            # Two-way members (ADR-0020): what the group changed in the channel. A refusal, a protected member and a person
            # nobody can place are told in the group once and counted here; a failed write is a member_failure like any other.
            "members_to_buzz": 0, "members_removed_from_buzz": 0, "members_refused": 0, "members_protected": 0,
            "members_unresolved": 0, "people_cache_failed": 0, "member_events_blocked": {},
            # A newly joined agent introduces itself once. Directory-only agents are honestly relayed by the Desk bot.
            "agent_intros_sent": 0, "agent_intros_relayed": 0, "agent_intro_failures": 0, "agent_intro_unknown": 0,
            "agent_intro_stopped": 0,
            # Two-way reactions (ADR-0020): Feishu reactions put on (or taken off) Buzz by the mirror, and join-request answers given
            # in Feishu. Only counted; a failure is counted in reactions_failed like any reaction.
            "reactions_to_buzz": 0, "reactions_withdrawn_in_buzz": 0, "approvals_to_buzz": 0,
            # Images are the point of a report, so a lost one (images_failed) needs a look; a policy skip (too large, over the
            # per-message limit, a format Feishu refuses ...) is counted by reason and does not.
            "images_to_feishu": 0, "images_to_buzz": 0, "images_failed": 0, "images_skipped": {}}


def needs_attention(report: Mapping[str, Any]) -> bool:
    return bool(report["errors"] or report["unknown"] or report["failed"] or report["blocked_bots"]
                or report["member_failures"] or report["removals_withheld"] or report["backlog_skipped"]
                or report["identity_conflicts"] or report["images_failed"])


def _skip(report: dict[str, Any], reason: str) -> None:
    report["skipped"][reason] = report["skipped"].get(reason, 0) + 1


@dataclass
class Round:
    cfg: Mapping[str, Any]
    clients: Clients
    state: State
    report: dict[str, Any]
    now: datetime
    persist: Callable[[], None]
    allow_bulk_removal: bool = False
    skip_backlog: bool = False  # the owner chose to drop a backlog one round cannot read
    roles: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    verified_agents: set[str] = field(default_factory=set)  # agent pubkeys whose profile is the configured app
    directory: dict[str, str] = field(default_factory=dict)  # channel agents this host has no configuration for -> their published app id
    introductions: dict[str, AgentIntroduction] = field(default_factory=dict)  # signed public profile/policy read this round
    id_to_pubkey: dict[str, str] = field(default_factory=dict)  # people's ids in the chosen identity space -> pubkey
    ambiguous_ids: set[str] = field(default_factory=set)  # ids that are some member's account, only not whose (a shared binding, two addresses)
    bot_members: dict[str, str] = field(default_factory=dict)
    preverified_listing: MemberListing | None = None  # read-only Desk gate, consumed by membership reconciliation
    owner_id: str = ""  # the owner's own id in that space: always in the group
    emails: dict[str, str] = field(default_factory=dict)  # pubkey -> address of the people that are mapped: in memory, never stored
    card_ctx: CardContext | None = None  # built when the round's first card needs it: the channel name is asked for once
    # One Buzz -> Feishu pass: what it has learned about the roots of threads whose replies have no parent to sit under.
    thread_lookups: int = 0  # threads read from Buzz so far
    thread_root_of: dict[str, str] = field(default_factory=dict)  # id a thread was asked about -> its root
    thread_lookup_failed: set[str] = field(default_factory=set)  # ids whose thread could not be read this round
    thread_handled: set[str] = field(default_factory=set)  # events the mirroring loop has taken up
    thread_backfilled: set[str] = field(default_factory=set)  # roots read from Buzz, mirrored only to give replies context
    thread_capped: bool = False  # a reply waits for a read the round's limit does not allow: the cursor stays
    # Two-way members (ADR-0020).
    other_mirrors: set[str] = field(default_factory=set)  # bot members whose owner declares them another host's mirror
    people_cache: dict[str, str] = field(default_factory=dict)  # the host-wide people cache as read this round
    member_keys: dict[str, str] = field(default_factory=dict)  # pubkeys that joined the channel this round -> their Feishu key
    agent_index: dict[str, str] = field(default_factory=dict)  # app ids looked up on the relay this round -> agent ("" if nobody's)
    agent_lookup_failed: bool = False  # a relay lookup failed this round: bots it could not place are asked about again next round
    signer_key: str = ""  # the people API's signer key, loaded when a member event is signed
    keep_gone: set[str] = field(default_factory=set)  # left the channel, removals withheld: kept in the snapshot for next round

    @property
    def now_ts(self) -> int:
        return int(self.now.timestamp())

    @property
    def desk_app_id(self) -> str:
        return self.cfg["agents"][self.cfg["desk_pubkey"]]["app_id"]

    def proxy_client(self) -> LarkCli:
        return self.clients.agents[self.desk_app_id]

    def outbound_client(self, out: Outbound, *, original: bool = False) -> LarkCli:
        app = out.via_app_id
        if app is None:
            app = self.state.b2f_senders.get(out.event_id) if original else self.desk_app_id
        if app not in self.clients.agents:
            raise GroupSyncError("message predates Desk cutover or its sender app is unavailable")
        return self.clients.agents[app]

    def verify_desk(self) -> None:
        desk = self.cfg["desk_pubkey"]
        if self.roles.get(desk) != "bot" or desk not in self.verified_agents:
            raise GroupSyncError("configured Desk is not a verified bot member of the Buzz Channel")
        user_id_type = "union_id" if self.union_mode else "open_id"
        self.preverified_listing = self.clients.owner.member_listing(self.cfg["chat_id"], user_id_type)
        self.bot_members = dict(self.preverified_listing.bots)
        if self.desk_app_id not in self.bot_members:
            raise GroupSyncError("configured Desk bot is not in the Feishu group")

    # -- identities and people ------------------------------------------------------------

    @property
    def union_mode(self) -> bool:
        return identity_mode(self.cfg) == "union_id"

    def verify_identities(self) -> None:
        cfg, clients = self.cfg, self.clients
        if not _owner_profile_ok(cfg, clients.owner):
            raise GroupSyncError("the owner's lark-cli profile is not the configured app and user")
        self.owner_id = cfg["owner_open_id"]
        if self.union_mode:
            # The owner's own union_id, from the owner's own token; it must be the configured owner's, so the account
            # that is always kept in the group is the one that was configured.
            open_id, union_id = clients.owner.user_info()
            if open_id != cfg["owner_open_id"] or not UNION_ID_RE.fullmatch(union_id):
                raise GroupSyncError("the owner's lark-cli user info is not the configured owner's, or has no union id")
            self.owner_id = union_id
        if clients.buzz.whoami() != cfg["mirror_pubkey"]:
            raise GroupSyncError("the mirror env file's key is not the configured mirror identity")
        for pubkey, agent in cfg["agents"].items():
            try:
                app_id, _ = clients.agents[agent["app_id"]].identity()
            except CliError:
                app_id = ""
            if app_id == agent["app_id"]:
                self.verified_agents.add(pubkey)
            else:
                self.report["errors"] += 1
                _skip(self.report, "agent_profile_mismatch")

    def load_people(self) -> None:
        """Who is in the channel, and which of them the bridge can name in Feishu: fresh every round."""
        buzz = self.clients.buzz
        members = buzz.members(self.cfg["channel_id"])
        self.roles = {m["pubkey"]: str(m.get("role") or "") for m in members}
        if self.roles.get(self.cfg["mirror_pubkey"]) != "bot":
            raise GroupSyncError("the mirror identity is not a bot member of the channel")
        self.names = buzz.names(list(self.roles))
        answer = fetch_people(self.cfg, self.roles, self.clients.http, self.now)
        # The bridge's open_ids (answer.open_ids) belong to the bridge's app: no person is ever told apart by them.
        self.ambiguous_ids = set()
        ids = (answer.union_ids or {}) if self.union_mode else self._open_ids_by_email(answer.emails or {})
        humans = self.humans()
        self.id_to_pubkey, shared = one_pubkey_per_id({pk: ids[pk] for pk in humans if ids.get(pk)})
        self.ambiguous_ids |= set(shared)
        # Two pubkeys with one Feishu account: who is who cannot be told, so neither is mapped (and a conflict is counted).
        self.report["identity_conflicts"] += len(shared)
        self.report["unmapped_members"] += len(humans) - len(self.id_to_pubkey)
        # Addresses only for people who are mapped (a shared account maps nobody), and only to name them in a card.
        mapped = set(self.id_to_pubkey.values())
        self.emails = {pk: addresses[0] for pk, addresses in (answer.emails or {}).items() if pk in mapped and addresses}

    def _open_ids_by_email(self, emails: Mapping[str, list[str]]) -> dict[str, str]:
        """email mode: {pubkey: this app's open_id} for the channel's people whose addresses lead to one account. An
        address is searched in the directory (exact matches only, see pick_open_id) and remembered by sha256(app id,
        address): a hit is trusted and never searched again, an address Feishu could not place is asked about again
        after EMAIL_MISS_RECHECK_SECONDS. Two addresses that lead to different accounts, a search that fails, or
        one that is over this round's budget: the person is not mapped (a conflict is counted)."""
        state, report, owner = self.state, self.report, self.clients.owner
        budget = EMAIL_LOOKUPS_PER_ROUND
        found_by_pubkey: dict[str, str] = {}
        for pubkey in sorted(self.humans()):
            found: set[str] = set()
            undecided = False
            for email in emails.get(pubkey) or []:
                key = hashlib.sha256(f"{self.cfg['owner_app_id']}\0{email}".encode()).hexdigest()
                known = state.emailmap.get(key)
                if known is not None and not known.startswith("miss:"):
                    found.add(known)
                    continue
                if known is not None and self.now_ts - int(known[len("miss:"):]) < EMAIL_MISS_RECHECK_SECONDS:
                    continue
                if budget <= 0:
                    undecided = True
                    break
                budget -= 1
                try:
                    open_id = pick_open_id(email, owner.search_user(email))
                except CliError:
                    report["errors"] += 1  # one failing search must not stop the round; nothing is remembered
                    undecided = True
                    break
                state.emailmap.pop(key, None)  # re-inserted, so the oldest entries are the ones that get pruned
                state.emailmap[key] = open_id or f"miss:{self.now_ts}"
                if open_id:
                    found.add(open_id)
            if len(found) > 1:
                report["identity_conflicts"] += 1
                self.ambiguous_ids |= found  # both accounts are his, and nobody can say which is speaking
            if len(found) == 1 and not undecided:
                found_by_pubkey[pubkey] = next(iter(found))
        return found_by_pubkey

    def humans(self) -> set[str]:
        """Channel people. A registered agent or the mirror is never a human, whatever its role."""
        machines = set(self.cfg["agents"]) | {self.cfg["mirror_pubkey"]}
        return {pk for pk, role in self.roles.items() if role in HUMAN_ROLES and pk not in machines}

    def bots_in_channel(self) -> set[str]:
        return {pk for pk, role in self.roles.items() if role == "bot" and pk != self.cfg["mirror_pubkey"]}

    def agents_in_channel(self) -> set[str]:
        """The channel's bot members that are agents: not this host's mirror, nor a mirror another host declared (ADR-0020)."""
        return self.bots_in_channel() - self.other_mirrors

    def load_directory(self) -> None:
        """The published Feishu app ids of the channel's agents this host has no configuration for (ADR-0019), asked only
        when there are such bots; the same answer tells which of them are other hosts' mirrors (ADR-0020). A failure leaves the
        directory empty for this round: those agents are still relayed."""
        foreign = self.bots_in_channel() - set(self.cfg["agents"])
        if not foreign:
            return
        reserved = {agent["app_id"] for agent in self.cfg["agents"].values()} | {self.cfg["owner_app_id"]}
        try:
            answer = fetch_agent_directory(self.cfg, self.clients.relay_url, foreign, reserved, self.clients.http, self.now)
        except GroupSyncError:
            self.report["directory_failed"] += 1
            self.agent_lookup_failed = True  # the relay is not answering this round: no further lookups either
            return
        self.other_mirrors = set(answer.mirrors)
        self.directory = dict(answer.apps)
        self.introductions.update(answer.introductions)
        self.report["directory_agents"] = len(self.directory)
        self.report["directory_conflicts"] = answer.conflicts

    def _directory_bots(self) -> dict[str, str]:
        """Directory agents whose bot is in the group: {pubkey: the bot's member id there}."""
        return {pk: self.bot_members[app] for pk, app in self.directory.items() if app in self.bot_members}

    # -- membership -------------------------------------------------------------------------

    def reconcile_members(self) -> None:
        cfg, owner, report, chat = self.cfg, self.clients.owner, self.report, self.cfg["chat_id"]
        user_id_type = "union_id" if self.union_mode else "open_id"
        # The group's own owner is never removed, whoever the channel's people are.
        group = owner.chat(chat, user_id_type)
        group_owner = str(group.get("owner_id") or "")
        protected = frozenset({group_owner}) if group.get("owner_id_type") == user_id_type and group_owner else frozenset()
        listing = self.preverified_listing or owner.member_listing(chat, user_id_type)
        self.preverified_listing = None
        actual_users, self.bot_members = set(listing.users), dict(listing.bots)
        two_way = membership_sync_mode(cfg) == "two_way"
        baseline = two_way and not self.state.members_synced
        delta = self.sync_members_both_ways(listing) if two_way else MemberDelta()
        # An agent whose profile could not be verified this round is left exactly as it is: its bot
        # is neither added nor removed (it counts as a foreign bot for capacity).
        managed = ({cfg["agents"][pk]["app_id"] for pk in self.verified_agents} | set(self.directory.values())
                   | delta.keep_bots)
        desired_bots = (({cfg["agents"][pk]["app_id"] for pk in self.agents_in_channel() & self.verified_agents}
                         | set(self.directory.values()) | delta.keep_bots)  # a directory agent is in the channel by construction
                        - delta.drop_bots - delta.hold_bots)
        desired_users = (set(self.id_to_pubkey) | delta.keep_users) - delta.drop_users - delta.hold_users
        # Two-way, the group is a source as well: somebody only in the group is not an "extra" any more (he may bind, and then
        # joins the channel). remove_extras only cleans up once, in the round that records the baseline; after that the
        # members who leave the channel are taken out of the group one by one (delta.remove_*).
        remove_extras = cfg["remove_extras"] and (baseline or not two_way)
        plan = plan_membership(desired_users=desired_users, desired_bots=desired_bots,
                               actual_users=actual_users, actual_bots=set(self.bot_members),
                               owner_open_id=self.owner_id, owner_app_id=cfg["owner_app_id"],
                               managed_bots=managed, remove_extras=remove_extras, protected=protected)
        withheld = report["removals_withheld"]
        plan, reason = guard_removals(plan, unmapped=report["unmapped_members"], allow_bulk=self.allow_bulk_removal)
        report["removals_withheld"] = reason or withheld
        if baseline and reason:
            self.state.members_synced = 0  # the one clean-up remove_extras still does is waiting: record the baseline after it
        keep = protected | {self.owner_id}
        plan = replace(plan,
                       remove_users=tuple(sorted(set(plan.remove_users) | {u for u in delta.remove_users if u in actual_users and u not in keep})),
                       remove_bots=tuple(sorted(set(plan.remove_bots) | {b for b in delta.remove_bots
                                                                          if b in self.bot_members and b != cfg["owner_app_id"]})))
        # The plan counted on its removals; if some were withheld, only add what still fits.
        room = max(MAX_BOTS_PER_CHAT - (len(self.bot_members) - len(plan.remove_bots)), 0)
        if len(plan.add_bots) > room:
            plan = replace(plan, add_bots=plan.add_bots[:room],
                           blocked_bots=tuple(sorted({*plan.blocked_bots, *plan.add_bots[room:]})))
        report["blocked_bots"] = len(plan.blocked_bots)
        # Removals first: a full group (15 bots) must make room before a new agent bot joins.
        steps = [("DELETE", plan.remove_bots, "app_id", BOTS_PER_REQUEST, "removed_bots"),
                 ("DELETE", plan.remove_users, user_id_type, USERS_PER_REQUEST, "removed_users"),
                 ("POST", plan.add_users, user_id_type, USERS_PER_REQUEST, "added_users"),
                 ("POST", plan.add_bots, "app_id", BOTS_PER_REQUEST, "added_bots")]
        changed = False
        for method, ids, id_type, size, counter in steps:
            for group in batches(list(ids), size):
                try:
                    failed = owner.change_members(method, chat, group, id_type)
                except CliError:
                    # One refused id must not stop the messages: count it and move on.
                    report["errors"] += 1
                    report["member_failures"] += len(group)
                    continue
                report["member_failures"] += failed
                report[counter] += len(group) - failed
                changed = changed or len(group) > failed
        if changed or delta.leaving:
            after = owner.member_listing(chat, user_id_type)
            self.bot_members = dict(after.bots)
            present = {self._user_key(u) for u in after.users} | {"b:" + app for app in after.bots}
            for pubkey, key in delta.leaving.items():
                if key in present:
                    self.keep_gone.add(pubkey)  # still in the group: taken out again next round
                else:
                    self.state.feishu_seen.pop(key, None)  # out, by our hand: when somebody pulls him back in, that is new
        if two_way:
            self._record_buzz_side(delta)

    # -- two-way members (ADR-0020) ---------------------------------------------------------

    def _user_key(self, member_id: str) -> str:
        return f"u:{member_id}" if self.union_mode else f"o:{self.cfg['owner_app_id']}:{member_id}"

    def _person(self, key: str) -> str:
        """The pubkey of a group member (a Feishu user key): a channel member the bridge vouches for now, then somebody this
        channel has seen before, then somebody another group sync on this host has seen (people_cache_file). "" if nobody."""
        current = self.id_to_pubkey.get(_key_member_id(key))
        if current:
            return current
        pubkey, _, seen = self.state.people_seen.get(key, "").partition("|")
        if pubkey and seen.isdigit() and self.now_ts - int(seen) <= PEOPLE_CACHE_TTL:
            return pubkey
        return self.people_cache.get(key) or ""

    def _agent(self, app_id: str, *, look_up: bool) -> str:
        """The agent pubkey of a group bot: this host's configuration, the directory, and — only when `look_up` and nothing
        closer knows it — every policy published on the relay (read once per round). "" if it is nobody's agent."""
        for pubkey, agent in self.cfg["agents"].items():
            if agent["app_id"] == app_id:
                return pubkey
        for pubkey, app in self.directory.items():
            if app == app_id:
                return pubkey
        if not look_up:
            return ""
        if app_id not in self.agent_index:
            if self.agent_lookup_failed:
                return ""  # the relay failed once this round: not asked again for every other bot
            reserved = {agent["app_id"] for agent in self.cfg["agents"].values()} | {self.cfg["owner_app_id"]}
            try:
                found = fetch_agent_index(self.cfg, self.clients.relay_url, reserved, self.clients.http, self.now,
                                          wanted=[app_id])
            except GroupSyncError:
                self.report["directory_failed"] += 1
                self.agent_lookup_failed = True
                return ""
            self.agent_index[app_id] = found.get(app_id, "")
            if self.agent_index[app_id]:
                # Keep the mapping for the rest of this round: an adopted directory agent can introduce itself immediately.
                self.directory[self.agent_index[app_id]] = app_id
        return self.agent_index[app_id]

    def _resolve(self, key: str, *, look_up: bool) -> str:
        return self._agent(key[2:], look_up=look_up) if key.startswith("b:") else self._person(key)

    def _key_of(self, pubkey: str) -> str:
        """A channel member's Feishu key, where it is known."""
        if pubkey in self.member_keys:
            return self.member_keys[pubkey]
        for member_id, owner in self.id_to_pubkey.items():
            if owner == pubkey:
                return self._user_key(member_id)
        agent = self.cfg["agents"].get(pubkey)
        if agent is not None:
            return "b:" + agent["app_id"]
        if pubkey in self.directory:
            return "b:" + self.directory[pubkey]
        return self.state.buzz_seen.get(pubkey, "")

    def _remember_people(self) -> None:
        fresh = {self._user_key(member_id): pubkey for member_id, pubkey in self.id_to_pubkey.items()}
        for key, pubkey in fresh.items():
            self.state.people_seen.pop(key, None)
            self.state.people_seen[key] = f"{pubkey}|{self.now_ts}"
        path = self.cfg.get("people_cache_file")
        if path:
            try:
                self.people_cache = merge_people_cache(Path(path), fresh, self.now_ts)
            except (OSError, GroupSyncError):
                self.report["people_cache_failed"] += 1

    def _signer(self) -> str:
        if not self.signer_key:
            self.signer_key = load_signer_key(Path(self.cfg["people_api"]["signer_env_file"]))
        return self.signer_key

    def _member_event(self, kind: int, pubkey: str, source_key: str, role: str | None = None) -> str:
        operation = _member_event_key(kind, pubkey, role, source_key)
        event = self.state.member_events.get(operation)
        fresh = event is None
        key = self._signer()
        signer = _signer_pubkey(key)
        if fresh:
            if len(self.state.member_events) >= MEMBER_EVENTS_MAX:
                raise MemberEventBlocked("ledger_full")
            if self.state.member_event_seq >= MEMBER_EVENT_SEQUENCE_MAX:
                raise MemberEventBlocked("sequence_exhausted")
            stream = self.state.member_event_stream or _new_member_event_stream()
            sequence = self.state.member_event_seq + 1
            tags = [["h", self.cfg["channel_id"]], ["p", pubkey]] + ([["role", role]] if role else [])
            tags += [[MEMBER_EVENT_NONCE_TAG, _new_member_event_nonce()], [MEMBER_EVENT_STREAM_TAG, stream],
                     [MEMBER_EVENT_SEQUENCE_TAG, str(sequence)]]
            event = sign_event(key, kind, tags, "", self.now_ts)
            self.state.member_event_stream = stream
            self.state.member_event_seq = sequence
            self.state.member_events[operation] = event
            self.persist()  # before the request: a crash or unknown response must retry this byte-for-byte event
        elif operation in self.state.member_event_blocks:
            raise MemberEventBlocked(self.state.member_event_blocks[operation])
        elif event["pubkey"] != signer:
            raise MemberEventBlocked("signer_changed")
        if abs(self.now_ts - event["created_at"]) > RELAY_CLOCK_SKEW_SECONDS:
            raise MemberEventBlocked("expired")
        try:
            publish_signed_event(self.clients.relay_url, key, event, self.clients.http, self.now)
        except RelayRefused:
            if fresh:
                # A first request that was definitively refused had no earlier unknown outcome, so it is safe to forget.
                self.state.member_events.pop(operation, None)
                self.persist()
                raise
            # This refusal says nothing about the earlier unknown request. Keep its exact event until state proves the effect
            # or the source intent is withdrawn; replacing it here could create a second side effect.
            self.state.member_event_blocks[operation] = "retry_refused"
            self.persist()
            raise MemberEventBlocked("retry_refused") from None
        except GroupSyncError:
            raise  # unknown: retain the exact signed event
        return operation  # the caller atomically saves its member snapshot and clears this ACK

    def _finish_member_event(self, operation: str) -> None:
        self.state.member_events.pop(operation, None)
        self.state.member_event_blocks.pop(operation, None)
        self.persist()

    def _settle_member_events(self, keys: Collection[str], *, complete: bool) -> None:
        """Drop pending operations whose effect is visible, or whose source-side intent was visibly reverted."""
        for operation in list(self.state.member_events):
            parsed = _parse_member_event_key(operation)
            if parsed is None:  # load_state already rejects it; keep this defensive for in-process mutations
                continue
            kind, pubkey, _, source_key = parsed
            applied = (kind == 9000 and pubkey in self.roles) or (kind == 9001 and pubkey not in self.roles)
            reverted = complete and ((kind == 9000 and source_key not in keys) or
                                     (kind == 9001 and source_key in keys))
            if applied or reverted:
                del self.state.member_events[operation]
                self.state.member_event_blocks.pop(operation, None)

    def _note_group(self, note: str, text: str) -> None:
        """Say something in the group once (from the Channel's Desk). The note is kept even when sending fails: a notice is not
        worth asking again every minute."""
        if note in self.state.member_notes:
            return
        self.state.member_notes[note] = self.now_ts
        try:
            self.proxy_client().send(self.cfg["chat_id"], text, "note-" + hashlib.sha256(note.encode()).hexdigest()[:40])
        except CliError:
            self.report["errors"] += 1

    def _membership_failure_reasons(self) -> list[str]:
        """Safe, actionable membership failures for the shared status message; never include server answers or identities."""
        report = self.report
        reasons: list[str] = []
        if report["directory_failed"]:
            reasons.append("Agent 目录读取失败：本轮未拉取远端 agent bot，下一轮自动重试。")
        withheld = report["removals_withheld"]
        if withheld == "member_list_incomplete":
            reasons.append("飞书成员列表未确认完整：已暂停按“列表中消失”从 Buzz 移人，下一轮自动重试。")
        elif withheld == "bulk_removal":
            reasons.append("待移出成员超过安全上限：已暂停删除；请核对后用 --allow-bulk-removal 执行一轮。")
        elif withheld == "unmapped_members":
            reasons.append("有频道成员无法映射到飞书身份：为避免误删已暂停移人；请先完成账号绑定。")
        if report["blocked_bots"]:
            reasons.append("飞书群 bot 名额不足：部分 agent bot 未能进群；请释放 bot 名额后重试。")
        if report["agent_intro_failures"] > report["agent_intro_stopped"]:
            reasons.append("Agent 自我介绍发送失败：公开资料或飞书发送暂不可用，下一轮自动重试。")
        if report["agent_intro_stopped"]:
            reasons.append("Agent 自我介绍连续发送失败：已停止自动重试；请让频道管理员检查 Agent 公开资料和飞书 bot 发送权限。")
        if report["agent_intro_unknown"]:
            reasons.append("Agent 自我介绍发送结果无法确认：为避免重复已停止自动重发；请检查群消息并联系频道管理员处理。")
        blocked = report["member_events_blocked"]
        if blocked.get("signer_changed"):
            reasons.append("未决成员变更的签名身份已变化：为避免重复操作已暂停重发；请恢复原签名身份，或人工核对 Buzz 频道成员后处理未决项。")
        if blocked.get("expired"):
            reasons.append("未决成员变更已超过 Relay 接收窗口：为避免生成第二条操作已暂停重发；请人工核对 Buzz 频道成员后处理未决项。")
        if blocked.get("ledger_full"):
            reasons.append("未决成员变更达到安全上限：已暂停未记账的新操作；请先恢复 Relay 并让现有操作收敛。")
        if blocked.get("sequence_exhausted"):
            reasons.append("成员变更序号已耗尽：已暂停新操作；请人工核对 Buzz 频道成员并联系维护者迁移同步 state。")
        if blocked.get("retry_refused"):
            reasons.append("Relay 拒绝了先前结果未知的成员变更重试：旧操作是否生效仍无法确认，已暂停该操作重试；请人工核对 Buzz 频道成员。")
        if report["member_failures"] > sum(blocked.values()):
            reasons.append("成员变更未完成：飞书成员接口或 Buzz Relay 未接受本轮操作，下一轮自动重试。")
        return reasons

    def _record_member_event_blocked(self, exc: MemberEventBlocked) -> None:
        self.report["errors"] += 1
        self.report["member_failures"] += 1
        blocked = self.report["member_events_blocked"]
        blocked[exc.reason] = blocked.get(exc.reason, 0) + 1

    def _link_member_notice(self, event_id: str, message_id: str) -> None:
        """Attach a direct Feishu fallback to its later Buzz root so the ordinary mirror never sends a duplicate."""
        self.state.b2f[event_id] = message_id
        self.state.b2f_modes[event_id] = SEND_TEXT_FALLBACK
        self.state.b2f_senders[event_id] = self.state.member_notice_sender or self.desk_app_id
        self.state.unresolved.pop(event_id, None)
        self.state.attempts.pop("b2f:" + event_id, None)

    def _direct_member_notice(self, content: str) -> None:
        state = self.state
        message_id = state.member_notice_feishu
        if not message_id and state.member_notice_event:
            message_id = feishu_id_for_buzz(state, state.member_notice_event) or ""
        if message_id and state.member_notice_sender != self.desk_app_id:
            # A pre-cutover notice cannot be edited by Desk. Re-root subsequent status under Desk once.
            if state.member_notice_event:
                state.b2f.pop(state.member_notice_event, None)
                state.b2f_modes.pop(state.member_notice_event, None)
                state.b2f_senders.pop(state.member_notice_event, None)
            state.member_notice_feishu = message_id = ""
            state.member_notice_sender = ""
            self.persist()
        try:
            if message_id:
                self.proxy_client().update_text(message_id, content)
            else:
                key = "membership-status-" + hashlib.sha256(self.cfg["channel_id"].encode()).hexdigest()[:32]
                state.member_notice_sender = self.desk_app_id
                self.persist()
                message_id = self.proxy_client().send(self.cfg["chat_id"], content, key)
        except CliError:
            self.report["errors"] += 1
            return
        state.member_notice_feishu = message_id
        if state.member_notice_event:
            self._link_member_notice(state.member_notice_event, message_id)

    def introduce_agents(self) -> None:
        """Introduce each agent once after it joins this binding.

        Configured agents use their own verified bot profile. A directory-only agent cannot safely lend this process its
        credentials, so the Desk bot labels the message as a proxy built from signed public metadata. State is persisted
        before the send and one deterministic Feishu idempotency key is reused while the result remains retryable.
        """
        present: dict[str, str] = {}
        for pubkey in self.agents_in_channel():
            configured = self.cfg["agents"].get(pubkey)
            app_id = configured["app_id"] if configured is not None and pubkey in self.verified_agents \
                else self.directory.get(pubkey)
            if app_id and app_id in self.bot_members:
                present[pubkey] = app_id
        state, report = self.state, self.report
        if not state.agent_intros_initialized:
            # Upgrade/binding baseline: every bot present while the feature is first enabled belongs to setup and does not
            # all speak at once. Subsequent joins are detected by their absence from this binding-scoped ledger.
            for pubkey in present:
                state.agent_intros.setdefault(pubkey, "baseline")
            state.agent_intros_initialized = True
        candidates = [pubkey for pubkey in sorted(present)
                      if pubkey not in state.agent_intros
                      or _is_pending(state.agent_intros.get(pubkey)) or _is_retry(state.agent_intros.get(pubkey))]
        if not candidates:
            return
        for pubkey in candidates:
            if pubkey not in state.agent_intros:
                state.agent_intros[pubkey] = _mark(RETRY, self.now_ts)
                state.agent_intro_senders[pubkey] = (present[pubkey] if present[pubkey] in self.clients.agents
                                                   else self.desk_app_id)
        self.persist()
        missing = [pubkey for pubkey in candidates if pubkey not in self.introductions]
        if missing:
            try:
                answer = fetch_agent_directory(self.cfg, self.clients.relay_url, missing, (), self.clients.http, self.now)
                self.introductions.update(answer.introductions)
            except GroupSyncError:
                pass  # configured agents can still introduce themselves with an explicitly unconfirmed response range
        for pubkey in candidates:
            value = state.agent_intros[pubkey]
            first = _marked_time(value) if _is_pending(value) or _is_retry(value) else self.now_ts
            if self.now_ts - first > FEISHU_RETRY_WINDOW_SECONDS:
                state.agent_intros[pubkey] = UNKNOWN
                report["errors"] += 1
                report["agent_intro_unknown"] += 1
                continue
            app_id = present[pubkey]
            own_profile = pubkey in self.verified_agents and app_id in self.clients.agents
            intro = self.introductions.get(pubkey)
            if intro is None and own_profile:
                intro = AgentIntroduction(self._display(pubkey), "", "")
            if intro is None:
                report["errors"] += 1
                report["agent_intro_failures"] += 1
                continue
            sender_app = app_id if own_profile else state.agent_intro_senders.get(pubkey)
            if sender_app not in self.clients.agents:
                state.agent_intros[pubkey] = UNKNOWN  # old proxy attempt is not retried through another identity
                report["errors"] += 1
                report["agent_intro_unknown"] += 1
                continue
            client = self.clients.agents[sender_app]
            text = render_agent_introduction(intro, relayed=not own_profile)
            key = "agent-intro-" + hashlib.sha256(
                f"{self.cfg['channel_id']}\0{self.cfg['chat_id']}\0{pubkey}".encode()).hexdigest()[:36]
            state.agent_intros[pubkey] = _mark(PENDING, first)
            state.agent_intro_senders[pubkey] = sender_app
            self.persist()
            try:
                message_id = client.send(self.cfg["chat_id"], text, key)
            except CliError as exc:
                report["errors"] += 1
                report["agent_intro_failures"] += 1
                if exc.definite:
                    attempt = "intro:" + pubkey
                    state.attempts[attempt] = state.attempts.get(attempt, 0) + 1
                    if state.attempts[attempt] >= MAX_SEND_ATTEMPTS:
                        state.agent_intros[pubkey] = FAILED
                        report["agent_intro_stopped"] += 1
                    else:
                        state.agent_intros[pubkey] = _mark(RETRY, first)
                # An unknown outcome stays pending and is retried only inside Feishu's idempotency window.
                self.persist()
                continue
            state.agent_intros[pubkey] = message_id
            state.attempts.pop("intro:" + pubkey, None)
            report["agent_intros_sent"] += 1
            if not own_profile:
                report["agent_intros_relayed"] += 1
            self.persist()

    def publish_membership_status(self) -> None:
        """Publish/update one canonical Buzz status, with a direct Feishu fallback when Buzz cannot be written.

        The fallback is linked to the eventual Buzz root in b2f, so recovery backfills the canonical event without creating
        a second group message. Every failing round emits a kind 40003 edit; the first healthy round edits the same root to
        say that synchronization recovered.
        """
        if membership_sync_mode(self.cfg) != "two_way":
            return
        state = self.state
        reasons = self._membership_failure_reasons()
        stamp = self.now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if reasons:
            state.member_notice_active = True
            content = f"飞书群成员同步失败（{stamp}）\n原因：\n- " + "\n- ".join(reasons)
        elif state.member_notice_active:
            state.member_notice_active = False
            content = f"飞书群成员同步已恢复（{stamp}）。本轮未发现需要人工处理的成员同步失败。"
        elif state.member_notice_feishu and not state.member_notice_event and state.member_notice_content:
            content = state.member_notice_content  # a prior direct fallback still needs its canonical Buzz root
        else:
            return
        state.member_notice_content = content
        tags = [["h", self.cfg["channel_id"]], [FEISHU_SYNC_STATUS_TAG, "membership"]]
        created_root = not state.member_notice_event
        try:
            if created_root:
                state.member_notice_event = self._mirror_publish(9, tags, content)
            else:
                self._mirror_publish(EDIT_KIND, [*tags, ["e", state.member_notice_event]], content)
        except GroupSyncError:
            # Direct Feishu delivery is the designed fallback, not a second user-visible failure. If that also fails,
            # _direct_member_notice records one error; the original membership problem is already present in the report.
            self._direct_member_notice(content)
            self.persist()
            return
        if created_root and state.member_notice_feishu:
            self._link_member_notice(state.member_notice_event, state.member_notice_feishu)
            self._direct_member_notice(content)  # refresh the fallback to this round's text; do not send another message
        self.persist()

    def _display(self, pubkey: str) -> str:
        if pubkey not in self.names:
            try:
                self.names.update(self.clients.buzz.names([pubkey]))
            except GroupSyncError:  # CliError is one
                pass
        return _safe_name(self.names.get(pubkey) or "") or pubkey[:12]

    def sync_members_both_ways(self, listing: MemberListing) -> MemberDelta:
        """Carry the group's changes since the last round into the channel, and work out which members that left the channel
        leave the group (ADR-0020). Buzz writes happen here; the group's are left to the plan in reconcile_members."""
        cfg, state, report = self.cfg, self.state, self.report
        if not listing.complete:
            report["removals_withheld"] = report["removals_withheld"] or "member_list_incomplete"
        mirror = cfg["mirror_pubkey"]
        keys = {self._user_key(u): ("user", u) for u in listing.users}
        keys.update({"b:" + app: ("bot", app) for app in listing.bots if app != cfg["owner_app_id"]})
        self._settle_member_events(keys, complete=listing.complete)
        names = {self._user_key(u): _safe_name(name) for u, name in listing.users.items()}
        buzz_now = {pk for pk in self.roles if pk != mirror and pk not in self.other_mirrors}
        self._remember_people()
        delta = MemberDelta()
        space = self._user_key("")
        if any(not key.startswith(("b:", space)) for key in state.feishu_seen):
            # The people's ids changed kind (identity union_id <-> email, or another owner app): every key would look like a
            # new member and a gone one at once. Nothing is carried over; the round records a new baseline instead.
            state.members_synced = 0
        if not state.members_synced:
            state.feishu_seen = {key: "" for key in keys}
            state.members_synced = self.now_ts
            return delta
        signer = _signer_pubkey(self._signer())
        added = [key for key in keys if key not in state.feishu_seen]
        removed = [key for key in state.feishu_seen if key not in keys] if listing.complete else []
        gone = [pk for pk in state.buzz_seen if pk not in buzz_now and pk not in self.other_mirrors]
        fresh = buzz_now - set(state.buzz_seen)
        for note in [n for n in state.member_notes if n.startswith("unresolved:") and n[len("unresolved:"):] not in keys]:
            del state.member_notes[note]
        adds: list[tuple[str, str, str]] = []
        if not listing.complete:
            for key in state.feishu_seen:
                if key not in keys:  # not listed, but the list was cut short: cannot tell, so not put back either
                    (delta.hold_bots if key.startswith("b:") else delta.hold_users).add(_key_member_id(key))
        for key in added:
            kind, _ = keys[key]
            pubkey = self._resolve(key, look_up=True)
            if not pubkey:
                if kind == "user":
                    # Not written down: once he can be placed (he binds, another channel here sees him) he joins the channel.
                    report["members_unresolved"] += 1
                    who = names.get(key) or "有人"
                    self._note_group(f"unresolved:{key}", (
                        f"{who} 还不是 Buzz 频道成员：认不出 TA 的 Buzz 账号（可能还没绑定）。请先在 "
                        f"{cfg['people_api']['base_url']}/bind/ 绑定，再请频道管理员在 Buzz 里把 TA 加进频道。"))
                elif not self.agent_lookup_failed:
                    state.feishu_seen[key] = ""  # a bot that is nobody's published agent: left alone, not looked up again
                continue  # (a failed lookup writes nothing: the bot is asked about again next round)
            state.member_notes.pop(f"unresolved:{key}", None)
            state.member_notes.pop(f"protected:{pubkey}", None)
            if pubkey in buzz_now or pubkey in state.buzz_seen or pubkey == mirror or pubkey in self.other_mirrors:
                state.feishu_seen[key] = ""
                continue  # a member already, or it left the channel this round and Buzz wins
            adds.append((key, pubkey, "bot" if kind == "bot" else "member"))
        removes: list[tuple[str, str]] = []
        for key in removed:
            state.member_notes.pop(f"refused:{key}", None)
            pubkey = self._resolve(key, look_up=False)
            if not pubkey or pubkey not in buzz_now or pubkey in fresh:
                del state.feishu_seen[key]  # nothing to carry: not a member, or it joined the channel this round (Buzz wins)
                continue
            if self.roles.get(pubkey) == "owner" or pubkey in (signer, mirror):
                report["members_protected"] += 1
                del state.feishu_seen[key]
                self._note_group(f"protected:{pubkey}", (
                    f"{self._display(pubkey)} 被移出了飞书群，但 TA 是 Buzz 频道的 owner 或本群同步所用的身份，"
                    "Buzz 频道里不会移出 TA。"))
                continue
            removes.append((key, pubkey))
        leaving = [(pk, state.buzz_seen.get(pk, "")) for pk in gone]
        leaving = [(pk, key) for pk, key in leaving if key in keys]
        self.keep_gone = set()
        if len(removes) + len(leaving) > BULK_REMOVAL_LIMIT and not self.allow_bulk_removal:
            report["removals_withheld"] = "bulk_removal"
            for key, _ in removes:
                (delta.hold_bots if key.startswith("b:") else delta.hold_users).add(_key_member_id(key))
            removes, leaving = [], []
            self.keep_gone = set(gone)  # still in the snapshots: the next round (with --allow-bulk-removal) finds them again
        for key, pubkey, role in adds:
            try:
                operation = self._member_event(9000, pubkey, key, role)
            except MemberEventBlocked as exc:
                self._record_member_event_blocked(exc)
                continue
            except RelayRefused as exc:
                if refused_by_policy(exc):
                    state.feishu_seen[key] = ""  # remember this appearance: only leaving and rejoining retries the policy
                    report["members_refused"] += 1
                    self._note_group(f"refused:{key}", (
                        f"{self._display(pubkey)} 没能加进 Buzz 频道：它的 owner 设置了不让别人把它拉进频道"
                        f"（{exc.reason.split(' ', 1)[0]}）。需要的话请联系它的 owner。"))
                else:
                    report["errors"] += 1
                    report["member_failures"] += 1
                continue
            except GroupSyncError:
                report["errors"] += 1
                report["member_failures"] += 1
                continue
            state.feishu_seen[key] = ""
            self.roles[pubkey] = role
            self.member_keys[pubkey] = key
            delta.adopted.append(pubkey)
            report["members_to_buzz"] += 1
            (delta.keep_bots if role == "bot" else delta.keep_users).add(_key_member_id(key))
            self._finish_member_event(operation)
        for key, pubkey in removes:
            try:
                operation = self._member_event(9001, pubkey, key)
            except MemberEventBlocked as exc:
                self._record_member_event_blocked(exc)
                (delta.hold_bots if key.startswith("b:") else delta.hold_users).add(_key_member_id(key))
                continue
            except GroupSyncError:  # RelayRefused is one: asked again next round (the key stays in the snapshot)
                report["errors"] += 1
                report["member_failures"] += 1
                (delta.hold_bots if key.startswith("b:") else delta.hold_users).add(_key_member_id(key))
                continue
            self.roles.pop(pubkey, None)
            del state.feishu_seen[key]
            delta.dropped.append(pubkey)
            report["members_removed_from_buzz"] += 1
            (delta.drop_bots if key.startswith("b:") else delta.drop_users).add(_key_member_id(key))
            self._finish_member_event(operation)
        for pubkey, key in leaving:
            (delta.remove_bots if key.startswith("b:") else delta.remove_users).add(_key_member_id(key))
            delta.leaving[pubkey] = key
        if delta.adopted or delta.dropped:
            self._announce(delta)
        return delta

    def _announce(self, delta: MemberDelta) -> None:
        """One line in the channel, from the mirror, about what the group changed this round."""
        parts = []
        if delta.adopted:
            parts.append("新加入 " + "、".join(self._display(pk) for pk in delta.adopted))
        if delta.dropped:
            parts.append("移出 " + "、".join(self._display(pk) for pk in delta.dropped))
        try:
            self.clients.buzz.send(self.cfg["channel_id"], "飞书群同步：" + "；".join(parts))
        except GroupSyncError:
            self.report["errors"] += 1

    def _record_buzz_side(self, delta: MemberDelta) -> None:
        """The channel's members as they are at the end of the round, with their Feishu keys: next round's baseline."""
        mirror = self.cfg["mirror_pubkey"]
        kept = {pk: self.state.buzz_seen[pk] for pk in self.keep_gone if pk in self.state.buzz_seen}
        self.state.buzz_seen = {pk: self._key_of(pk) for pk in self.roles if pk != mirror and pk not in self.other_mirrors}
        self.state.buzz_seen.update(kept)

    # -- shared send bookkeeping ----------------------------------------------------------

    def _close(self, ledger: dict[str, str], unresolved: dict[str, int], item: str) -> None:
        """An open attempt that will not be retried: pending may have been delivered (unknown),
        a refusal was not (failed). Reported once."""
        if _is_pending(ledger.get(item)):
            ledger[item] = UNKNOWN
            self.report["unknown"] += 1
        else:
            ledger[item] = FAILED
            self.report["failed"] += 1
        unresolved.pop(item, None)

    def _refused(self, direction: str, ledger: dict[str, str], unresolved: dict[str, int], item: str,
                 created: int, first_attempt: int, extra: str | None = None) -> None:
        """A definite refusal: nothing was sent, so the item is retried (it stays unresolved,
        which keeps it inside the next round's read window) until MAX_SEND_ATTEMPTS."""
        self.report["errors"] += 1
        key = f"{direction}:{item}"
        self.state.attempts[key] = self.state.attempts.get(key, 0) + 1
        if self.state.attempts[key] >= MAX_SEND_ATTEMPTS:
            ledger[item] = FAILED
            unresolved.pop(item, None)
            self.report["failed"] += 1
        else:
            ledger[item] = _mark(RETRY, first_attempt, extra)
            unresolved[item] = created

    def _give_up_stale(self, ledger: dict[str, str], unresolved: dict[str, int]) -> None:
        """Unresolved items older than the discovery window can no longer be re-read: close them out
        once, loudly, instead of keeping them pending forever."""
        for item, created in list(unresolved.items()):
            if self.now_ts - created > THREAD_DISCOVERY_SECONDS:
                self._close(ledger, unresolved, item)

    # -- Buzz -> Feishu ---------------------------------------------------------------------

    def _agent_apps(self) -> dict[str, str]:
        """Agents that speak in Feishu with their own bot: in the channel, the configured profile verified,
        and that app's bot is a member of the group."""
        cfg = self.cfg
        return {pk: cfg["agents"][pk]["app_id"] for pk in self.agents_in_channel() & self.verified_agents
                if cfg["agents"][pk]["app_id"] in self.bot_members}

    def _card_open_ids(self, agent_apps: Mapping[str, str]) -> dict[str, str]:
        """pubkey -> that person's open_id in the owner's app, where it is known: in union mode from the pairing Feishu itself
        gave (idmap); in email mode nobody needs one, since a person is only mapped there through an address, which is
        what a card names them by. An agent's bot is a member of the group under its own member id. Never a union_id."""
        ids: dict[str, str] = {}
        if self.union_mode:
            opens = {union: open_id for open_id, union in self.state.idmap.items()}
            ids = {pk: opens[union] for union, pk in self.id_to_pubkey.items() if union in opens}
        ids.update({pk: self.bot_members[app] for pk, app in agent_apps.items()})
        ids.update(self._directory_bots())  # a directory agent's bot is named the same way (ADR-0019)
        return ids

    def _card_context(self, agent_apps: Mapping[str, str]) -> CardContext:
        """What a card needs besides the event, made once per round and only once a card is really going out: the channel's
        name (a card's byline) is asked for then, and a failure only costs the channel on the byline."""
        if self.card_ctx is None:
            try:
                name = self.clients.buzz.channel_name(self.cfg["channel_id"])
            except GroupSyncError:  # CliError is one
                name = ""
            self.card_ctx = CardContext(link_base=self.cfg["people_api"]["base_url"], channel_id=self.cfg["channel_id"],
                                        channel_name=name, emails=self.emails,
                                        open_ids={})
        return self.card_ctx

    def _buzz_edit(self, event: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]],
                   agent_apps: Mapping[str, str], mention_targets: Mapping[str, tuple[str, str]],
                   agent_mention_targets: Mapping[str, tuple[str, str]], unmapped_mode: str,
                   unmanaged_mode: str, managed_agents: set[str]) -> None:
        """Apply one Buzz kind 40003 to the Feishu copy of its original event. The replacement is rendered with the
        original event's routing/thread/mention metadata and the edit's content, then sent by the same bot in place."""
        state, report = self.state, self.report
        edit_id, created = str(event["id"]), int(event.get("created_at") or 0)
        value = state.e2f.get(edit_id)
        if value in (FAILED, UNKNOWN) or _settled(value):
            state.edit_unresolved.pop(edit_id, None)
            return
        open_attempt = _is_pending(value) or _is_retry(value)
        if open_attempt and self.now_ts - _marked_time(value) > FEISHU_RETRY_WINDOW_SECONDS:
            self._close(state.e2f, state.edit_unresolved, edit_id)
            return
        target = edit_target(event)
        if target is None:
            _skip(report, "edit_no_single_target")
            return
        original = by_id.get(target)
        if original is None:
            try:
                original = self.clients.buzz.thread_event(self.cfg["channel_id"], target)
            except GroupSyncError:
                report["errors"] += 1
                _skip(report, "edit_target_unavailable")
                return
        if str(original.get("kind")) not in {"9", "45001", "45003"}:
            _skip(report, "edit_target_kind")
            return
        if str(original.get("pubkey") or "") != str(event.get("pubkey") or ""):
            _skip(report, "edit_foreign")
            return
        message_id = feishu_id_for_buzz(state, target)
        mode = state.b2f_modes.get(target)
        if message_id is None:
            _skip(report, "edit_target_unmirrored")
            return
        if mode not in (SEND_CARD, SEND_TEXT_FALLBACK):
            _skip(report, "edit_mode_unknown")
            return
        replacement = {**original, "content": str(event.get("content") or "")}
        if self.state.b2f_senders.get(target) not in self.clients.agents:
            _skip(report, "edit_legacy_sender")
            return
        out = route_buzz_event(replacement, mirror_pubkey=self.cfg["mirror_pubkey"], agent_apps=agent_apps,
                               human_pubkeys=self.humans(), agent_pubkeys=self.agents_in_channel(), names=self.names,
                               mention_targets={}, unmapped_senders=unmapped_mode,
                               agent_mention_targets={}, unmanaged_agents=unmanaged_mode,
                               managed_agents=managed_agents,
                               card=self._card_context(agent_apps) if mode == SEND_CARD else None)
        if isinstance(out, str):
            _skip(report, "edit_" + out)
            return
        client = self.outbound_client(out, original=True)
        first = _marked_time(value) if open_attempt else self.now_ts
        state.e2f[edit_id] = _mark(PENDING, first, _edit_extra(target, message_id, mode))
        state.edit_unresolved[edit_id] = created
        self.persist()
        try:
            if mode == SEND_CARD:
                client.update_card(message_id, out.card)
            else:
                client.update_text(message_id, out.text)
        except CliError as exc:
            if exc.definite:
                self._refused("e2f", state.e2f, state.edit_unresolved, edit_id, created, first,
                              _edit_extra(target, message_id, mode))
            else:
                report["unknown"] += 1
            return
        state.e2f[edit_id] = message_id
        state.edit_unresolved.pop(edit_id, None)
        state.attempts.pop("e2f:" + edit_id, None)
        report["messages_updated"] += 1

    @staticmethod
    def _post(client: LarkCli, chat_id: str, parent: str | None, body: str, key: str, *, card: bool) -> str:
        """One message into the group, or into the thread of `parent`: a card (`body` is its JSON) or plain text."""
        if card:
            return client.reply_card(parent, body, key) if parent else client.send_card(chat_id, body, key)
        return client.reply(parent, body, key) if parent else client.send(chat_id, body, key)

    def buzz_to_feishu(self) -> None:
        cfg, state, report = self.cfg, self.state, self.report
        agent_apps = self._agent_apps()
        mention_targets = {pk: (person_id, self.names.get(pk) or pk[:12]) for person_id, pk in self.id_to_pubkey.items()}
        agent_mention_targets = {pk: (self.bot_members[app], self.names.get(pk) or pk[:12]) for pk, app in agent_apps.items()}
        agent_mention_targets.update({pk: (member, self.names.get(pk) or pk[:12])
                                      for pk, member in self._directory_bots().items()})
        mention_targets.update(agent_mention_targets)
        unmapped_mode = buzz_unmapped_sender_mode(cfg)  # read every round: narrowing it takes effect at once
        unmanaged_mode = buzz_unmanaged_agent_mode(cfg)
        managed_agents = set(cfg["agents"])
        mirrored_from_feishu = set(state.f2b.values())

        def route(event: Mapping[str, Any]) -> Outbound | str:
            return route_buzz_event(event, mirror_pubkey=cfg["mirror_pubkey"], agent_apps=agent_apps,
                                    human_pubkeys=self.humans(), agent_pubkeys=self.agents_in_channel(),
                                    names=self.names, mention_targets={}, unmapped_senders=unmapped_mode,
                                    agent_mention_targets={},
                                    unmanaged_agents=unmanaged_mode, managed_agents=managed_agents,
                                    other_mirrors=self.other_mirrors)

        self._give_up_stale(state.b2f, state.unresolved)
        self._give_up_stale(state.e2f, state.edit_unresolved)
        self._give_up_stale_images()
        floor = max(state.floor, state.buzz_floor)
        since = max(state.buzz_since - BUZZ_OVERLAP_SECONDS, floor, 0)
        open_times = [*state.unresolved.values(), *state.edit_unresolved.values(), *state.img_unresolved.values()]
        if open_times:  # reach back far enough to re-read every retry and pending send, of a text or of an image
            since = max(min(since, min(open_times)), floor, 0)
        try:
            fetched = self.clients.buzz.messages(cfg["channel_id"], since)
        except BuzzBacklogError:
            if not self.skip_backlog:
                raise
            report["backlog_skipped"].append("buzz")
            for item in list(state.unresolved):
                state.b2f[item] = UNKNOWN if _is_pending(state.b2f.get(item)) else FAILED
                state.unresolved.pop(item)
            for item in list(state.edit_unresolved):
                state.e2f[item] = UNKNOWN if _is_pending(state.e2f.get(item)) else FAILED
                state.edit_unresolved.pop(item)
            for item in list(state.img_unresolved):
                state.images[item] = UNKNOWN if _is_pending(state.images.get(item)) else FAILED
                state.img_unresolved.pop(item)
            state.buzz_floor = self.now_ts  # everything up to now is dropped, and never read again
            fetched = []
        by_id = {str(e["id"]): e for e in fetched}
        queue = deque(sorted(fetched, key=buzz_message_order))
        while queue:  # a reply whose thread root has no Feishu copy is put back behind that root (_thread_root_parent)
            event = queue.popleft()
            event_id, created = str(event["id"]), int(event.get("created_at") or 0)
            if event_id in self.thread_backfilled:
                created = self.now_ts  # read from Buzz only as a reply's context: not history, whatever its age
            self.thread_handled.add(event_id)
            if created < state.floor:
                _skip(report, "before_binding")
                continue
            if int(event.get("kind") or 0) == EDIT_KIND:
                self._buzz_edit(event, by_id, agent_apps, mention_targets, agent_mention_targets, unmapped_mode,
                                unmanaged_mode, managed_agents)
                continue
            attached = any(isinstance(t, list) and t[:1] == ["imeta"] for t in event.get("tags") or [])
            value = state.b2f.get(event_id)
            if value in (FAILED, UNKNOWN) or _settled(value) or event_id in mirrored_from_feishu:
                state.unresolved.pop(event_id, None)
                if attached and _settled(value):
                    routed = route(event)  # the text went out earlier (or in the last round): its images may still be to do
                    if state.b2f_senders.get(event_id) not in self.clients.agents:
                        self._drop_images(event, "legacy_sender")
                    elif isinstance(routed, Outbound):
                        self._images_to_feishu(routed, created)
                    else:  # the sender cannot speak any more: what is left is not sent by anyone else
                        self._drop_images(event, routed)
                elif attached and value in (FAILED, UNKNOWN):
                    # A terminal text with images still to do, closed on some path that could not settle them (a backlog dropped on
                    # purpose, a state from before images): no confirmed caption, so the images stay out, counted once.
                    self._drop_images(event, "text_not_sent")
                continue
            open_attempt = _is_pending(value) or _is_retry(value)
            if open_attempt and state.b2f_senders.get(event_id) not in self.clients.agents:
                state.b2f[event_id] = UNKNOWN
                state.unresolved.pop(event_id, None)
                _skip(report, "legacy_send_stopped")
                if attached:
                    self._drop_images(event, "legacy_sender")
                continue
            if open_attempt and self.now_ts - _marked_time(value) > FEISHU_RETRY_WINDOW_SECONDS:
                self._close(state.b2f, state.unresolved, event_id)  # past the idempotency window: never resend
                if attached:  # no confirmed caption, and the event is not read again: the images stay out, counted now
                    self._drop_images(event, "text_not_sent")
                continue
            out = route(event)
            if isinstance(out, str):
                _skip(report, out)
                if open_attempt:  # cannot be sent any more: close it now rather than keep it open
                    self._close(state.b2f, state.unresolved, event_id)
                    if attached:
                        self._drop_images(event, "text_not_sent")
                continue
            client = self.outbound_client(out, original=open_attempt)
            if open_attempt:
                # A retry repeats the first attempt exactly: same first time, same endpoint (send or
                # reply to the same parent), same key, and the same request: a card stays a card and text stays text,
                # whatever message_format says by now (a card that was replaced by text stays that text).
                first = _marked_time(value)
                parent, mode = _parse_send_extra(_marked_extra(value))
            else:
                first, mode = self.now_ts, (SEND_CARD if message_format(cfg) == "card" else "")
                parent = feishu_id_for_buzz(state, out.parent_event_id) if out.parent_event_id else None
                if parent is None and out.parent_event_id:
                    parent = self._thread_root_parent(event, out.parent_event_id, queue, by_id)
                    if parent is _HELD:
                        continue
            if mode == SEND_CARD:  # only a message that is really going out as a card asks for the card's context
                out = route_buzz_event(event, mirror_pubkey=cfg["mirror_pubkey"], agent_apps=agent_apps,
                                       human_pubkeys=self.humans(), agent_pubkeys=self.agents_in_channel(),
                                       names=self.names, mention_targets={}, unmapped_senders=unmapped_mode,
                                       agent_mention_targets={},
                                       unmanaged_agents=unmanaged_mode, managed_agents=managed_agents,
                                       other_mirrors=self.other_mirrors, card=self._card_context(agent_apps))
            state.b2f[event_id] = _mark(PENDING, first, _send_extra(parent, mode))
            state.b2f_senders[event_id] = out.via_app_id or self.desk_app_id
            state.unresolved[event_id] = created
            self.persist()  # an unknown outcome is retried only under the same idempotency key
            key = "b2f-" + hashlib.sha256(event_id.encode()).hexdigest()[:40]
            try:
                if mode == SEND_CARD:
                    try:
                        message_id = self._post(client, cfg["chat_id"], parent, out.card, key, card=True)
                        report["cards_sent"] += 1
                    except CliError as exc:
                        if not card_refused(exc):
                            raise  # refused for another reason, or not known to be refused: as for text
                        # Feishu said the card is wrong, so it did not go out: say it as text, under a key of its own.
                        # From here on this message is text (the mark says so), even if the text has no luck at once.
                        mode = SEND_TEXT_FALLBACK
                        report["cards_fallback_text"] += 1
                        state.b2f[event_id] = _mark(PENDING, first, _send_extra(parent, mode))
                        self.persist()
                        message_id = self._post(client, cfg["chat_id"], parent, out.text, key + TEXT_FALLBACK_KEY_SUFFIX, card=False)
                else:
                    message_id = self._post(client, cfg["chat_id"], parent, out.text,
                                            key + (TEXT_FALLBACK_KEY_SUFFIX if mode == SEND_TEXT_FALLBACK else ""), card=False)
            except CliError as exc:
                if exc.definite:
                    self._refused("b2f", state.b2f, state.unresolved, event_id, created, first, _send_extra(parent, mode))
                    if attached and state.b2f[event_id] == FAILED:  # given up on the text: so are its images
                        self._drop_images(event, "text_not_sent")
                else:
                    report["unknown"] += 1  # stays pending and unresolved: retried next round
                continue
            state.b2f[event_id] = message_id
            state.b2f_modes[event_id] = SEND_CARD if mode == SEND_CARD else SEND_TEXT_FALLBACK
            state.unresolved.pop(event_id, None)
            state.attempts.pop("b2f:" + event_id, None)
            state.attempts.pop("b2f-thread:" + event_id, None)
            self._watch(message_id, event_id, str(event.get("content") or ""))
            if parent:
                state.threads[parent] = self.now_ts  # our reply opened or continued this thread
                state.polled.setdefault(parent, state.feishu_since)  # replies since the last round count
            report["to_feishu"] += 1
            if out.relayed:
                report["relayed_agents"] += 1
            if event_id in self.thread_backfilled:
                report["thread_roots_backfilled"] += 1
            if out.images:  # where the text went is where the images go, this round or a later one (not recomputed from the parent)
                state.images[f"{event_id}:thread"] = parent or "-"
            self._images_to_feishu(out, created)
        # Wall clock, not the newest created_at: a fast client clock cannot push the cursor
        # ahead, and the 900s overlap still catches events a slow clock stamped in the past.
        # A reply deferred by the thread-read limit keeps it where it is (as a capped reaction pass does).
        if not self.thread_capped:
            state.buzz_since = self.now_ts

    def _thread_root_parent(self, event: Mapping[str, Any], parent_id: str, queue: deque, by_id: Mapping[str, Any]) -> Any:
        """A reply whose direct parent has no Feishu copy goes under its thread's root instead, and a root that has none is
        sent first, as any mirrored message is, however old it is: only threads with a new reply get their root.
        Returns the Feishu message to reply under, None to send the reply at top level (the root cannot be there, or the
        thread cannot be read), or _HELD: the reply is put back behind its root, or waits in `unresolved` until the root's
        send is settled (an unknown outcome is retried under its key, never bypassed) or the round's read limit allows."""
        state, report = self.state, self.report
        event_id, created = str(event["id"]), int(event.get("created_at") or 0)
        named = _buzz_root(event)
        key = named or parent_id  # any message of the thread will do to ask for it
        root_id = named or self.thread_root_of.get(key)
        if root_id is None:
            top = by_id.get(parent_id)
            if top is not None and _buzz_parent(top) is None:
                root_id = parent_id  # the direct parent is the top of the thread, and it was read with this reply
        root_event: Mapping[str, Any] | None = by_id.get(root_id) if root_id else None
        if root_id is not None:
            copy = feishu_id_for_buzz(state, root_id)
            if copy:
                return copy
        if root_event is None and (root_id is None or root_id not in self.thread_handled):
            found = self._read_thread_root(event, key, named)
            if not isinstance(found, dict):
                return found
            root_id = str(found["id"])
            self.thread_root_of[key] = root_id  # a root that already has a copy is found again at the top of the next visit
            root_event = by_id.get(root_id, found)
            if root_id not in by_id:
                self.thread_backfilled.add(root_id)
        if root_id not in self.thread_handled:
            if root_id in by_id:
                queue.remove(root_event)  # read with this reply, but sorted behind it: it goes first
            queue.appendleft(event)
            queue.appendleft(root_event)
            return _HELD
        value = state.b2f.get(root_id)
        if _is_pending(value) or _is_retry(value):
            state.unresolved[event_id] = created  # the root is being retried under its own key: this reply comes after it
            return _HELD
        report["thread_root_unavailable"] += 1  # skipped by the routing, or given up on: the reply is not lost, it goes out alone
        return None

    def _read_thread_root(self, event: Mapping[str, Any], key: str, expect: str | None) -> Any:
        """Ask Buzz for the root of the thread holding `key`: the root event, _HELD while the round's limit or a failed read
        makes the reply wait, or None once the read has failed MAX_SEND_ATTEMPTS times for this reply (it goes out alone)."""
        state, report = self.state, self.report
        event_id, created = str(event["id"]), int(event.get("created_at") or 0)
        if key not in self.thread_lookup_failed:
            if self.thread_lookups >= THREAD_ROOT_LOOKUPS_PER_ROUND:
                state.unresolved[event_id] = created
                report["thread_roots_deferred"] += 1
                self.thread_capped = True
                return _HELD
            self.thread_lookups += 1
            try:
                return self.clients.buzz.thread_root(self.cfg["channel_id"], key, expect=expect)
            except GroupSyncError:
                self.thread_lookup_failed.add(key)  # one read per thread per round, failed or not
                report["errors"] += 1
                report["thread_root_failed"] += 1
        attempts = "b2f-thread:" + event_id
        state.attempts[attempts] = state.attempts.get(attempts, 0) + 1
        if state.attempts[attempts] >= MAX_SEND_ATTEMPTS:
            state.attempts.pop(attempts)
            return None
        state.unresolved[event_id] = created
        return _HELD

    def _skip_image(self, reason: str, count: int = 1) -> None:
        skipped = self.report["images_skipped"]
        skipped[reason] = skipped.get(reason, 0) + count

    def _close_image(self, item: str) -> None:
        """An open image attempt that will not be retried: pending may have been delivered (unknown), a refusal was not (failed)."""
        state = self.state
        if _is_pending(state.images.get(item)):
            state.images[item] = UNKNOWN
            self.report["unknown"] += 1
        else:
            state.images[item] = FAILED
            self.report["images_failed"] += 1
        state.img_unresolved.pop(item, None)
        state.attempts.pop("b2f-img:" + item, None)

    def _give_up_stale_images(self) -> None:
        for item, created in list(self.state.img_unresolved.items()):
            if self.now_ts - created > THREAD_DISCOVERY_SECONDS:
                self._close_image(item)  # the event cannot be read any more

    def _drop_images(self, event: Mapping[str, Any], reason: str) -> None:
        """The attachments of an event that cannot go out (no confirmed text, no sender): each one still to do is closed as
        skipped, once, with this reason."""
        state = self.state
        images, over, _ = event_images(event)
        event_id = str(event["id"])
        for n in range(len(images)):
            item = f"{event_id}:{n}"
            value = state.images.get(item)
            if value in (FAILED, UNKNOWN, SKIPPED) or _settled(value):
                continue
            state.images[item] = SKIPPED
            state.img_unresolved.pop(item, None)
            state.attempts.pop("b2f-img:" + item, None)
            self._skip_image(reason)
        if over and f"{event_id}:over" not in state.images:
            state.images[f"{event_id}:over"] = SKIPPED
            self._skip_image(reason, over)

    def _image_refused(self, item: str, created: int, first: int) -> None:
        """An image that could not be fetched or was refused: nothing reached Feishu, so it is tried again next round (from the
        same first attempt, endpoint and key) until MAX_SEND_ATTEMPTS, then given up with one count."""
        state = self.state
        self.report["errors"] += 1
        key = "b2f-img:" + item
        state.attempts[key] = state.attempts.get(key, 0) + 1
        if state.attempts[key] >= MAX_SEND_ATTEMPTS:
            state.images[item] = FAILED
            state.img_unresolved.pop(item, None)
            state.attempts.pop(key)
            self.report["images_failed"] += 1
        else:
            state.images[item] = _mark(RETRY, first)
            state.img_unresolved[item] = created

    def _relay_image(self, ref: ImageRef, workdir: Path, n: int) -> str:
        """Download one attachment with the mirror identity, check it (bytes are an image Feishu takes, within the size limit, and
        the very blob the event named) and return its file name inside `workdir`, generated here and named by what it is."""
        raw = workdir / f"dl-{n}"
        self.clients.buzz.download_media(ref.segment, raw)
        data, kind = read_image(raw, allowed=IMAGE_FEISHU_FORMATS)
        if hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ImageSkip("hash_mismatch")
        name = f"img-{n}{IMAGE_EXTENSIONS[kind]}"
        os.replace(raw, workdir / name)
        return name

    def _images_to_feishu(self, out: Outbound, created: int) -> None:
        """The attachments of an event whose text is out, one image message each, from the same sender and into the same thread as
        the text. Each image has its own ledger entry and idempotency key, so a partly delivered event only gets what is missing;
        a failure of one image never touches the text or the others."""
        cfg, state, report = self.cfg, self.state, self.report
        if out.images_over and f"{out.event_id}:over" not in state.images:
            state.images[f"{out.event_id}:over"] = SKIPPED  # counted once, however often the event is read
            self._skip_image("over_limit", out.images_over)
        if not out.images:
            return
        client = self.outbound_client(out, original=True)
        thread = state.images.get(f"{out.event_id}:thread")  # the Feishu message the text was sent under ("-": none)
        if thread is None:  # the text went out before the images were synced: the direct parent's copy is the best guess
            parent = feishu_id_for_buzz(state, out.parent_event_id) if out.parent_event_id else None
        else:
            parent = None if thread == "-" else thread
        workdir: Path | None = None
        try:
            for n, ref in enumerate(out.images):
                item = f"{out.event_id}:{n}"
                value = state.images.get(item)
                if value in (FAILED, UNKNOWN, SKIPPED) or _settled(value):
                    continue
                if isinstance(ref, str):  # an attachment that cannot be fetched at all
                    state.images[item] = SKIPPED
                    self._skip_image(ref)
                    continue
                if _is_pending(value) or _is_retry(value):
                    # A retry repeats the first attempt exactly: same endpoint (send, or reply to the same parent), same key.
                    first = _marked_time(value)
                    if self.now_ts - first > FEISHU_RETRY_WINDOW_SECONDS:
                        self._close_image(item)  # past the idempotency window: never resend
                        continue
                else:
                    first = self.now_ts
                try:
                    workdir = workdir or Path(tempfile.mkdtemp(prefix="fgs-images-"))  # 0700
                    name = self._relay_image(ref, workdir, n)
                except ImageSkip as skip:
                    state.images[item] = SKIPPED
                    state.img_unresolved.pop(item, None)
                    state.attempts.pop("b2f-img:" + item, None)
                    self._skip_image(skip.reason)
                    continue
                except (CliError, OSError):  # a failed download, or a local disk that is full or not writable
                    self._image_refused(item, created, first)
                    continue
                state.images[item] = _mark(PENDING, first)
                state.img_unresolved[item] = created
                self.persist()  # an unknown outcome is retried only under the same idempotency key
                key = "b2f-img-" + hashlib.sha256(item.encode()).hexdigest()[:36]
                try:
                    if parent:
                        message_id = client.reply_image(parent, name, key, cwd=workdir)
                    else:
                        message_id = client.send_image(cfg["chat_id"], name, key, cwd=workdir)
                except CliError as exc:
                    if exc.definite:
                        self._image_refused(item, created, first)
                    else:
                        report["unknown"] += 1  # stays pending: retried next round under the same key
                    continue
                finally:
                    try:
                        (workdir / name).unlink(missing_ok=True)
                    except OSError:
                        pass  # the directory goes as a whole below
                state.images[item] = message_id
                state.img_unresolved.pop(item, None)
                state.attempts.pop("b2f-img:" + item, None)
                report["images_to_feishu"] += 1
        finally:
            if workdir is not None:
                shutil.rmtree(workdir, ignore_errors=True)

    # -- Buzz reactions -> Feishu -----------------------------------------------------------

    # -- two-way reactions (ADR-0020) -------------------------------------------------------

    def _watch(self, message_id: str, event_id: str, content: str = "") -> None:
        """A message with a copy on both sides: its Feishu reactions are read for a while (a join request for a week)."""
        if reaction_sync_mode(self.cfg) != "two_way":
            return
        span = JOIN_REQUEST_WATCH_SECONDS if JOIN_HEADER_RE.search(content) else REACTION_WATCH_SECONDS
        watch = self.state.rwatch
        watch.pop(message_id, None)
        watch[message_id] = f"{event_id}|{self.now_ts + span}"
        if len(watch) > REACTION_WATCH_MAX:  # the ones that stop being watched soonest go first
            for gone in sorted(watch, key=lambda m: int(watch[m].rsplit("|", 1)[1]))[:len(watch) - REACTION_WATCH_MAX]:
                del watch[gone]

    def _mirror_publish(self, kind: int, tags: list[list[str]], content: str, created_at: int | None = None) -> str:
        """An event signed here as the mirror. Like the Buzz CLI's own events it carries the mirror's NIP-OA auth tag (clients
        show an agent's words under its owner by it), and the request carries it as x-auth-tag (relay membership)."""
        key = secret_hex(self.clients.mirror_key, "mirror env file")
        try:
            auth = json.loads(self.clients.mirror_auth_tag) if self.clients.mirror_auth_tag else None
        except ValueError:
            auth = None
        if isinstance(auth, list) and len(auth) >= 2 and auth[0] == "auth" and all(isinstance(x, str) for x in auth):
            tags = [*tags, auth]
        return publish_event(self.clients.relay_url, key, kind, tags, content, self.clients.http, self.now,
                             auth_tag=self.clients.mirror_auth_tag or None, created_at=created_at)

    def _approval_tags(self, inbound: Inbound, reply_to: str | None) -> list[list[str]] | None:
        """A member's `/approve JOIN-<id>` / `/deny JOIN-<id>` under a Buzz thread (ADR-0020): the extra tags it goes out with, or
        None when it is not one. Such a reply is signed here instead of by `buzz messages send` (which cannot add a tag), naming the
        person and the request, the form the join-request script takes a Feishu answer in. (Not a reaction: the relay keeps one
        reaction per identity, target and emoji, so somebody else's ✅ would block it.)"""
        if reaction_sync_mode(self.cfg) != "two_way" or not reply_to or inbound.context_only or inbound.image_keys:
            return None
        head, sep, body = inbound.text.partition("：")
        found = APPROVAL_COMMAND_RE.fullmatch(body.strip()) if sep and head.startswith("[飞书] ") else None
        if found is None:
            return None
        return [[FEISHU_AUTHOR_TAG, inbound.sender_pubkey], ["join", found.group(2)]]

    def feishu_reactions_to_buzz(self) -> None:
        """People's Feishu reactions on the watched messages, said in Buzz by the mirror (kind 7 with the person's pubkey in a
        feishu-author tag); one taken back in Feishu is taken back in Buzz (kind 5). Bots' reactions never come back (they came from
        Buzz), nor do those of people nobody can place. Decoration: failures are counted, never alarmed on."""
        cfg, state, report = self.cfg, self.state, self.report
        if reaction_sync_mode(cfg) != "two_way":
            return
        state.rwatch = {m: v for m, v in state.rwatch.items() if int(v.rsplit("|", 1)[1]) > self.now_ts}
        if not state.rwatch:
            return
        reverse = reverse_reaction_map(merged_reaction_map(cfg.get("reaction_map") or {}))
        id_type = "union_id" if self.union_mode else "open_id"
        calls = 0
        for group in batches(list(state.rwatch), REACTION_QUERY_BATCH):
            try:
                found = self.clients.owner.reaction_details(group, id_type)
            except CliError:
                report["reactions_failed"] += 1
                continue
            for message_id, items in found.items():
                event_id = state.rwatch[message_id].split("|", 1)[0]
                present: set[str] = set()
                for _, operator_id, emoji_type in items:
                    # A bot's reaction is listed by its app id, which is nobody's id here: it came from Buzz and never goes back.
                    pubkey = self.id_to_pubkey.get(operator_id)
                    if not pubkey:
                        continue
                    item = f"{message_id}|{operator_id}|{emoji_type}"
                    present.add(item)
                    said = state.f2r.get(item, "")
                    if said and not said.startswith(PENDING):
                        continue
                    emoji = reverse.get(emoji_type)
                    if emoji is None:
                        state.f2r[item] = SKIPPED
                        _skip(report, "reaction_emoji_unmapped")
                        continue
                    # The relay keeps one reaction per identity, target and emoji: a second person's same emoji shares it.
                    shared = next((v for k, v in state.f2r.items() if k != item and k.startswith(message_id + "|")
                                   and k.endswith("|" + emoji_type) and HEX64_RE.fullmatch(v)), None)
                    if shared:
                        state.f2r[item] = shared
                        continue
                    if calls >= REACTIONS_PER_ROUND:
                        continue
                    calls += 1
                    first = _marked_time(said) if said else self.now_ts  # a retry is the same event: a lost answer makes no copy
                    state.f2r[item] = _mark(PENDING, first)
                    try:
                        state.f2r[item] = self._mirror_publish(7, [["e", event_id], [FEISHU_AUTHOR_TAG, pubkey]], emoji,
                                                               created_at=first)
                    except RelayRefused:
                        report["reactions_failed"] += 1
                        state.f2r[item] = FAILED
                        continue
                    except GroupSyncError:
                        report["reactions_failed"] += 1
                        if self.now_ts - first > RELAY_CLOCK_SKEW_SECONDS:
                            state.f2r[item] = FAILED  # the relay would not take a retry this old any more
                        continue
                    report["reactions_to_buzz"] += 1
                for item in [k for k in state.f2r if k.startswith(message_id + "|") and k not in present]:
                    said = state.f2r[item]
                    if not HEX64_RE.fullmatch(said):
                        del state.f2r[item]  # never said in Buzz (skipped, failed): nothing to take back
                        continue
                    if any(v == said for k, v in state.f2r.items() if k != item and k in present):
                        del state.f2r[item]  # somebody else's same reaction still stands on it
                        continue
                    if calls >= REACTIONS_PER_ROUND:
                        continue
                    calls += 1
                    try:
                        self._mirror_publish(5, [["e", said]], "")
                    except GroupSyncError:
                        report["reactions_failed"] += 1
                        continue
                    del state.f2r[item]
                    report["reactions_withdrawn_in_buzz"] += 1

    def _reaction_failed(self, key: str, ledger_value: str, item: str) -> None:
        """A refused or uncertain reaction call: tried again next round (the create is idempotent), given
        up after MAX_SEND_ATTEMPTS with one count."""
        state = self.state
        state.attempts[key] = state.attempts.get(key, 0) + 1
        if state.attempts[key] >= MAX_SEND_ATTEMPTS:
            state.r2f[item] = ledger_value
            state.attempts.pop(key)
            self.report["reactions_failed"] += 1

    def buzz_reactions_to_feishu(self) -> None:
        """An agent's Buzz reaction (kind 7) becomes the matching Feishu emoji on the mirrored copy of the
        message, from the agent's own bot; withdrawing it (kind 5) removes it. Humans' reactions are not synced.
        Runs after the messages, so a reaction finds its target already mirrored."""
        cfg, state, report = self.cfg, self.state, self.report
        agent_apps, humans, agents = self._agent_apps(), self.humans(), self.agents_in_channel()
        reaction_map = merged_reaction_map(cfg.get("reaction_map") or {})
        # Two-way (ADR-0020): the channel's people, and the agents whose messages the owner's bot relays, react through that bot.
        relay_authors = (humans | (agents - set(cfg["agents"]))) if reaction_sync_mode(cfg) == "two_way" else set()
        floor = max(state.floor, state.buzz_floor)
        since = max(state.react_since - BUZZ_OVERLAP_SECONDS, floor, 0)
        try:
            fetched = self.clients.buzz.messages(cfg["channel_id"], since, kinds=REACTION_KINDS)
        except BuzzBacklogError:
            # Decoration is not worth a stuck round: drop what cannot be read, and say so.
            report["backlog_skipped"].append("reactions")
            state.react_since = self.now_ts
            return
        withdrawn: dict[str, set[str]] = {}  # reaction event id -> authors of a deletion that names it
        for event in fetched:
            if str(event.get("kind")) == "5":
                for tag in event.get("tags") or []:
                    if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "e" and isinstance(tag[1], str):
                        withdrawn.setdefault(tag[1], set()).add(str(event.get("pubkey") or ""))
        calls, capped = 0, False
        for event in sorted(fetched, key=buzz_reaction_order):
            event_id, created = str(event["id"]), int(event.get("created_at") or 0)
            kind = str(event.get("kind"))
            if kind == "5":
                for tag in event.get("tags") or []:
                    target = tag[1] if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "e" else None
                    value = state.r2f.get(target) if isinstance(target, str) else None
                    if not value or value in (FAILED, REMOVED):
                        continue  # not a reaction we made, or already settled
                    parts = value.split("|")
                    pubkey, message_id, emoji_type, reaction_id = parts[:4]
                    if pubkey != event.get("pubkey"):
                        _skip(report, "reaction_delete_foreign")  # only the reaction's own author can withdraw it
                        continue
                    if calls >= REACTIONS_PER_ROUND:
                        capped = True
                        continue
                    if len(parts) == 5 and (parts[4] == LEGACY_OWNER_REACTION or parts[4].startswith("desk:")):
                        # One proxy reaction stands for everybody who reacted so in Buzz: it goes with the last.
                        proxy_marker = parts[4]
                        if proxy_marker == LEGACY_OWNER_REACTION:
                            state.r2f[target] = REMOVED
                            _skip(report, "reaction_legacy_sender")
                            continue
                        others = [v for item, v in state.r2f.items() if item != target and v.count("|") == 4
                                  and v.split("|")[1:3] == [message_id, emoji_type] and v.endswith("|" + proxy_marker)]
                        if not others:
                            sender = self.clients.agents.get(proxy_marker[5:])
                            if sender is None:
                                # The Desk app that created this reaction has been removed or rotated.
                                # Keep the Feishu reaction intact and close this withdrawal explicitly;
                                # a different bot cannot remove that app's reaction.
                                state.r2f[target] = FAILED
                                state.attempts.pop("r2f-del:" + target, None)
                                _skip(report, "reaction_sender_unavailable")
                                report["reactions_failed"] += 1
                                continue
                            calls += 1
                            try:
                                sender.unreact(message_id, reaction_id)
                            except CliError:
                                self._reaction_failed("r2f-del:" + target, REMOVED, target)
                                continue
                            report["reactions_removed"] += 1
                        state.r2f[target] = REMOVED
                        state.attempts.pop("r2f-del:" + target, None)
                        continue
                    agent = cfg["agents"].get(pubkey)
                    if agent is None:
                        state.r2f[target] = REMOVED
                        continue
                    calls += 1
                    try:
                        self.clients.agents[agent["app_id"]].unreact(message_id, reaction_id)
                    except CliError:
                        self._reaction_failed("r2f-del:" + target, REMOVED, target)
                        continue
                    state.r2f[target] = REMOVED
                    state.attempts.pop("r2f-del:" + target, None)
                    report["reactions_removed"] += 1
                continue
            if created < state.floor:
                _skip(report, "before_binding")
                continue
            if state.r2f.get(event_id):
                continue  # done, refused for good, or withdrawn
            if event.get("pubkey") in withdrawn.get(event_id, ()):
                state.r2f[event_id] = REMOVED  # added and withdrawn between two rounds: never shown
                _skip(report, "reaction_withdrawn")
                continue
            routed = route_buzz_reaction(event, agent_apps=agent_apps, human_pubkeys=humans, agent_pubkeys=agents,
                                         reaction_map=reaction_map, relay_authors=relay_authors,
                                         proxy_app_id=self.desk_app_id, mirror_pubkey=cfg["mirror_pubkey"],
                                         other_mirrors=self.other_mirrors)
            if isinstance(routed, str):
                _skip(report, routed)
                continue
            app_id, emoji_type = routed
            via_proxy = str(event.get("pubkey") or "") not in agent_apps and app_id == self.desk_app_id
            message_id = feishu_id_for_buzz(state, reaction_target(event) or "")
            if message_id is None:
                _skip(report, "reaction_target_unmirrored")  # read again next round while it is inside the window
                continue
            if calls >= REACTIONS_PER_ROUND:
                capped = True
                continue
            calls += 1
            try:
                reaction_id = self.clients.agents[app_id].react(message_id, emoji_type)
            except CliError:
                self._reaction_failed("r2f:" + event_id, FAILED, event_id)
                continue
            state.r2f[event_id] = "|".join((str(event["pubkey"]), message_id, emoji_type, reaction_id,
                                            *(("desk:" + app_id,) if via_proxy else ())))
            state.attempts.pop("r2f:" + event_id, None)
            report["reactions_added"] += 1
        if not capped:  # otherwise the same window is read again, and what is done is in r2f
            state.react_since = self.now_ts

    # -- Feishu -> Buzz ---------------------------------------------------------------------

    def _feishu_images(self, message_id: str, keys: tuple[str, ...], workdir: Path | None) -> tuple[list[str], dict[str, int], int]:
        """The images of one Feishu message, ready to attach: downloaded with the owner's login into a private slot, judged by
        their content (an image Buzz's relay takes, within the size limit), stripped of metadata, and written under names generated
        here. Returns (file paths, policy skips by reason, images that failed). A failure — the download, or a local disk that is full
        or not writable (`workdir` None: there is none) — costs that image only. Nothing is counted in the report yet: a message
        whose send is refused is tried again and would count twice."""
        files: list[str] = []
        skipped: dict[str, int] = {}
        failed = 0

        def skip(reason: str, count: int = 1) -> None:
            skipped[reason] = skipped.get(reason, 0) + count

        if len(keys) > IMAGES_PER_EVENT:
            skip("over_limit", len(keys) - IMAGES_PER_EVENT)
        for n, key in enumerate(keys[:IMAGES_PER_EVENT]):
            if workdir is None:
                failed += 1
                continue
            slot = workdir / f"in-{n}"
            try:
                slot.mkdir(mode=0o700)
                data, kind = read_image(self.clients.owner.download_image(message_id, key, slot), allowed=IMAGE_BUZZ_FORMATS)
                clean = strip_metadata(data, kind)
                out = workdir / f"up-{n}{IMAGE_EXTENSIONS[kind]}"
                fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(clean)
                files.append(str(out))
            except ImageSkip as reason:
                skip(reason.reason)
            except ValueError:
                skip("bad_image")
            except (CliError, OSError):
                failed += 1
            finally:
                shutil.rmtree(slot, ignore_errors=True)
        return files, skipped, failed

    def feishu_to_buzz(self) -> None:
        cfg, state, owner, report = self.cfg, self.state, self.clients.owner, self.report
        bot_member_to_pubkey = {self.bot_members[cfg["agents"][pk]["app_id"]]: pk
                                for pk in self.agents_in_channel() & self.verified_agents
                                if cfg["agents"][pk]["app_id"] in self.bot_members}
        bot_member_to_pubkey.update({member: pk for pk, member in self._directory_bots().items()})
        allowed_senders = sender_allowlist(cfg)  # read from the config every round: narrowing it takes effect at once
        unmapped_mode = unmapped_sender_mode(cfg)  # so does this
        floor = max(state.floor, state.feishu_floor)
        window_start = max(state.feishu_since - FEISHU_OVERLAP_SECONDS, floor)
        mirrored_from_buzz = set(state.b2f.values())
        self._give_up_stale(state.f2b, state.f_unresolved)
        waiting: dict[str, int] = {}  # a stranger's messages that wait for the re-read window this round: id -> created

        def at(seconds: int) -> datetime:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)

        def mirror(msg: Mapping[str, Any], root: str | None, since: int) -> None:
            message_id, created = str(msg.get("message_id") or ""), _feishu_ts(msg)
            if not message_id or created is None:
                return
            value = state.f2b.get(message_id)
            if value in (FAILED, UNKNOWN) or _settled(value) or message_id in mirrored_from_buzz:
                state.f_unresolved.pop(message_id, None)
                return
            if _is_pending(value):  # left by a crash mid-send; the Buzz CLI has no idempotency key
                state.f2b[message_id] = UNKNOWN
                state.f_unresolved.pop(message_id, None)
                report["unknown"] += 1
                return
            if not _is_retry(value) and created < since:
                return  # old messages only feed thread discovery; they are never backfilled
            resolver = (IdResolver(owner, state, report, msg, set(self.bot_members.values()))
                        if self.union_mode else None)
            inbound = route_feishu_message(msg, open_id_to_pubkey=self.id_to_pubkey,
                                           bot_member_to_pubkey=bot_member_to_pubkey,
                                           channel_members=set(self.roles), names=self.names, now=self.now,
                                           resolve_id=resolver, allowed_senders=allowed_senders,
                                           unmapped_senders=unmapped_mode, ambiguous_ids=frozenset(self.ambiguous_ids))
            first = _marked_time(value) if _is_retry(value) else self.now_ts
            if resolver is not None and resolver.error and (isinstance(inbound, Inbound) or inbound == "unmapped_sender"):
                # Asking Feishu who is in this message failed, so nothing was decided and nothing was sent: retry it like
                # a refused send. That holds when the sender could not be resolved ("unmapped_sender") and also when the
                # sender is known (cached) and only a mentioned person could not be — the message is a valid Inbound
                # then, but sending it would silently drop that @. A message skipped for another reason (empty ...) is not retried.
                self._refused("f2b", state.f2b, state.f_unresolved, message_id, created, first)
                return
            if resolver is not None and resolver.conflict:
                inbound = "identity_conflict"
            elif resolver is not None and isinstance(inbound, str) and inbound == "unmapped_sender" and resolver.sender_unpaired():
                inbound = "sender_unpaired"
            if isinstance(inbound, str):
                _skip(report, inbound)
                if _is_retry(value):  # cannot be sent any more: close it now
                    self._close(state.f2b, state.f_unresolved, message_id)
                return
            if inbound.context_only:
                if self.now_ts - created <= FEISHU_OVERLAP_SECONDS:
                    # A stranger's words go out only once they are older than the re-read window: somebody who is merely unmapped for
                    # a moment (a binding the bridge does not show yet, a failed address search, a union_id not backfilled) is read
                    # again by the next round and then goes out as himself, mentions and all — the way "skip" has always healed.
                    # Until then nothing is written for the message, and it is not counted.
                    waiting[message_id] = created
                    return
            elif (inbound.mentions and any(c <= created for c in waiting.values())) or (root is not None and root in waiting):
                # A member's call to an agent that comes after a waiting stranger's words, or a reply under one, waits with them: said
                # first, the agent it wakes would not find what it is about (or the reply would land outside the thread). Such a message
                # is younger than the window like the words it waits for, so the next round reads it again.
                return
            # A stranger's mentions are matched locally against the channel's bots, never against Feishu's identity answer,
            # so a person he @-mentioned that Feishu could not pair costs a context-only message nothing.
            for _ in range(resolver.mentions_unpaired() if resolver is not None and not inbound.context_only else 0):
                _skip(report, "mention_unpaired")
            reply_to = buzz_id_for_feishu(state, root) if root else None
            files: list[str] = []
            skipped_images: dict[str, int] = {}
            failed_images = 0
            workdir: Path | None = None
            try:
                if inbound.image_keys:
                    try:
                        workdir = Path(tempfile.mkdtemp(prefix="fgs-images-"))  # 0700
                    except OSError:  # no disk to put them on: the images fail, the text does not wait for them
                        workdir = None
                    # An image that cannot be had costs that image only: the text and the other images go now (there is no idempotency
                    # key for a Buzz send, so an image cannot be added to the message later).
                    files, skipped_images, failed_images = self._feishu_images(message_id, inbound.image_keys, workdir)
                state.f2b[message_id] = _mark(PENDING, first)
                state.f_unresolved[message_id] = created
                self.persist()

                def settled_images() -> None:  # counted when the message is done, not per attempt
                    for reason, count in skipped_images.items():
                        self._skip_image(reason, count)
                    report["images_failed"] += failed_images

                extra = self._approval_tags(inbound, reply_to)
                try:
                    if extra is None:
                        event_id = self.clients.buzz.send(cfg["channel_id"], inbound.text, reply_to=reply_to,
                                                          mentions=inbound.mentions, files=tuple(files))
                    else:
                        # The first attempt's time, so a retry after a lost answer is the same event (the relay has it).
                        tags = ([["h", cfg["channel_id"]], ["e", reply_to, "", "reply"]] + [["p", pk] for pk in inbound.mentions]
                                + extra)
                        try:
                            event_id = self._mirror_publish(9, tags, inbound.text, created_at=first)
                        except RelayRefused:
                            raise CliError("send", 0, "rejected", definite=True) from None
                        except GroupSyncError:
                            raise CliError("send", -1, "unknown", definite=False) from None
                        report["approvals_to_buzz"] += 1
                except CliError as exc:
                    if exc.definite:
                        self._refused("f2b", state.f2b, state.f_unresolved, message_id, created, first)
                        if state.f2b[message_id] == FAILED:  # given up: what is known about its images is settled with it
                            settled_images()
                    elif extra is not None:
                        # Signed here with the first attempt's time: resending is the same event, so it is simply retried.
                        self._refused("f2b", state.f2b, state.f_unresolved, message_id, created, first)
                    else:
                        state.f2b[message_id] = UNKNOWN  # maybe delivered: never resend
                        state.f_unresolved.pop(message_id, None)
                        report["unknown"] += 1
                        settled_images()
                    return
                state.f2b[message_id] = event_id
                state.f_unresolved.pop(message_id, None)
                state.attempts.pop("f2b:" + message_id, None)
                report["to_buzz"] += 1
                self._watch(message_id, event_id)
                if inbound.context_only:
                    report["context_to_buzz"] += 1
                report["images_to_buzz"] += len(files)
                settled_images()
            finally:
                if workdir is not None:
                    shutil.rmtree(workdir, ignore_errors=True)

        start = window_start
        if state.f_unresolved:  # reach back far enough to re-read every refused message
            start = max(min(start, min(state.f_unresolved.values())), floor)
        rows, more = owner.messages(cfg["chat_id"], at(start), order="asc", page_limit=FEISHU_PAGE_LIMIT)
        if more:
            if not self.skip_backlog:
                raise GroupSyncError("more Feishu messages since the last round than one round reads; refusing to "
                                     "skip any (run one round with --skip-backlog to drop them on purpose)")
            report["backlog_skipped"].append("feishu")
            for item in list(state.f_unresolved):
                state.f2b[item] = UNKNOWN if _is_pending(state.f2b.get(item)) else FAILED
                state.f_unresolved.pop(item)
            rows = []  # nothing up to now is mirrored, and never read again
            state.feishu_floor = floor = window_start = self.now_ts
        recent, _ = owner.messages(cfg["chat_id"], at(max(self.now_ts - THREAD_DISCOVERY_SECONDS, floor)),
                                   order="desc", page_limit=DISCOVERY_PAGE_LIMIT)
        for msg in [*rows, *recent]:
            root = str(msg.get("message_id") or "")
            if msg.get("thread_id") and root and root not in state.threads:
                state.threads[root] = self.now_ts  # a newly seen thread is polled first
                state.polled.setdefault(root, state.feishu_since)  # its replies since the last round count
        for msg in rows:
            mirror(msg, None, window_start)

        active = [(root, act) for root, act in state.threads.items() if self.now_ts - act <= THREAD_MAX_AGE_SECONDS]
        hot = [root for root, _ in sorted(active, key=lambda kv: kv[1], reverse=True)[:THREAD_HOT]]
        # A thread that keeps failing goes to the back of the rotation instead of blocking it.
        cold = sorted((root for root, _ in active if root not in hot),
                      key=lambda r: max(state.polled.get(r, 0), state.tried.get(r, 0)))[:THREAD_ROTATE]
        for root in [*hot, *cold]:
            # Each thread keeps its own cursor, so a thread polled only now and then loses nothing.
            since = max(state.polled.get(root, state.feishu_since) - FEISHU_OVERLAP_SECONDS, floor)
            try:
                replies, more = owner.thread_messages(root)
            except CliError:
                report["errors"] += 1  # one broken thread must not stop the others
                state.tried[root] = self.now_ts
                continue
            times = [t for t in (_feishu_ts(m) for m in replies if str(m.get("message_id") or "") != root) if t is not None]
            if more and times and min(times) >= since:
                # More new replies than one read holds: mirror what we have, keep the cursor, say so.
                report["errors"] += 1
                state.tried[root] = self.now_ts
            else:
                state.polled[root] = self.now_ts
                state.tried.pop(root, None)
            for msg in sorted(replies, key=lambda m: (_feishu_ts(m) or 0, str(m.get("message_id") or ""))):
                if str(msg.get("message_id") or "") == root:
                    continue
                created = _feishu_ts(msg)
                if created is not None:
                    state.threads[root] = max(state.threads[root], min(created, self.now_ts))
                mirror(msg, root, since)
        state.feishu_since = self.now_ts  # a message skipped earlier is never backfilled later


def _owner_only(meta: os.stat_result) -> bool:
    return meta.st_uid == os.getuid() and not meta.st_mode & 0o077


def _lock(state_dir: Path) -> Any:
    """The state dir must be the owner's own directory (not a symlink, no group/other access);
    the lock is a 0600 regular file opened without following symlinks."""
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    meta = os.lstat(state_dir)  # lstat: a symlink is not a directory
    if not stat.S_ISDIR(meta.st_mode) or not _owner_only(meta):
        raise GroupSyncError("state dir must be an owner-only (0700) directory, not a symlink")
    try:
        fd = os.open(state_dir / LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o600)
    except OSError:
        raise GroupSyncError("round lock could not be opened as an owner-only regular file") from None
    handle = os.fdopen(fd, "a")
    lock_meta = os.fstat(fd)
    if not stat.S_ISREG(lock_meta.st_mode) or not _owner_only(lock_meta):
        handle.close()
        raise GroupSyncError("round lock must be an owner-only (0600) regular file")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise GroupSyncError("another round is running for this state dir") from None
    return handle


def round_command(config_path: Path, state_dir: Path, *, base_env: Mapping[str, str], runner: Any = subprocess.run,
                  now: datetime | None = None, allow_bulk_removal: bool = False,
                  skip_backlog: bool = False, http: Any = None) -> dict[str, Any]:
    cfg = load_config(config_path)
    if not cfg["chat_id"]:
        raise GroupSyncError("config has no chat_id yet: run create-chat or bind first")
    now = now or datetime.now(timezone.utc)
    state_dir = Path(state_dir)
    lock = _lock(state_dir)
    try:
        clients = _clients(cfg, base_env, runner, http)
        state = load_state(state_dir)
        binding = f"{cfg['channel_id']}|{cfg['chat_id']}"
        if state.binding and state.binding != binding:
            raise GroupSyncError("this state dir belongs to another channel or chat")
        report = _new_report()
        if unmapped_sender_mode(cfg) == "context":
            report["context_to_buzz"] = 0  # only a channel that mirrors strangers has this count (the report keeps its shape otherwise)
        run = Round(cfg, clients, state, report, now, lambda: save_state(state_dir, state), allow_bulk_removal,
                    skip_backlog)
        try:
            run.verify_identities()
            run.load_people()
            run.load_directory()
        except GroupSyncError as exc:
            # Nothing has started, so nothing is written: a round that cannot say who is who (the bridge is
            # down, the signer is not an owner or admin) neither changes a group nor fixes the binding's start.
            exc.report = report
            raise
        try:
            # Validate the required sender before any membership mutation, notice, or state baseline.
            run.verify_desk()
            if not state.binding:  # the first verified round starts the binding: nothing older is mirrored
                state.binding, state.floor = binding, int(now.timestamp()) - FEISHU_OVERLAP_SECONDS
                state.buzz_since = state.feishu_since = state.react_since = state.floor
            run.reconcile_members()
            run.introduce_agents()
            run.publish_membership_status()
            run.buzz_to_feishu()
            run.feishu_to_buzz()
            run.buzz_reactions_to_feishu()
            run.feishu_reactions_to_buzz()
        except GroupSyncError as exc:
            exc.report = report
            raise
        finally:
            prune_state(state)
            save_state(state_dir, state)
        return report
    finally:
        lock.close()


def main(argv: list[str] | None = None, *, base_env: Mapping[str, str] | None = None, runner: Any = subprocess.run,
         stdout: Any = sys.stdout, now: datetime | None = None, http: Any = None) -> int:
    parser = argparse.ArgumentParser(description="Buzz channel <-> Feishu group (references/feishu-group-sync.md)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("preflight")
    p.add_argument("--config", required=True)
    p.add_argument("--mode", choices=("new", "existing"), required=True)
    p.add_argument("--chat-id")
    c = sub.add_parser("create-chat")
    c.add_argument("--config", required=True)
    c.add_argument("--name", required=True)
    b = sub.add_parser("bind")
    b.add_argument("--config", required=True)
    b.add_argument("--chat-id", required=True)
    m = sub.add_parser("migrate-desk")
    m.add_argument("--config", required=True)
    m.add_argument("--desk-pubkey", required=True)
    m.add_argument("--apply", action="store_true")
    r = sub.add_parser("round")
    r.add_argument("--config", required=True)
    r.add_argument("--state-dir", required=True)
    r.add_argument("--allow-bulk-removal", action="store_true")
    r.add_argument("--skip-backlog", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ if base_env is None else base_env)
    config = Path(args.config)
    try:
        if args.command == "preflight":
            out: Any = preflight_command(config, args.mode, args.chat_id, base_env=env, runner=runner)
            code = EXIT_OK if out["ok"] else EXIT_BLOCKED
        elif args.command == "create-chat":
            out, code = create_chat_command(config, args.name, base_env=env, runner=runner), EXIT_OK
        elif args.command == "bind":
            out, code = bind_command(config, args.chat_id, base_env=env, runner=runner), EXIT_OK
        elif args.command == "migrate-desk":
            out, code = migrate_desk_config(config, args.desk_pubkey, apply=args.apply, now=now), EXIT_OK
        else:
            out = round_command(config, Path(args.state_dir), base_env=env, runner=runner, now=now,
                                allow_bulk_removal=args.allow_bulk_removal, skip_backlog=args.skip_backlog, http=http)
            code = EXIT_ATTENTION if needs_attention(out) else EXIT_OK
    except GroupSyncError as exc:
        if exc.report is not None:
            print(json.dumps(exc.report, ensure_ascii=False), file=stdout)
        hint = f" ({AUTH_HINT})" if isinstance(exc, CliError) and exc.kind in {"authentication", "token_missing"} else ""
        print(f"buzz_feishu_group_sync: {exc}{hint}", file=sys.stderr)
        return EXIT_ERROR
    print(json.dumps(out, ensure_ascii=False), file=stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
