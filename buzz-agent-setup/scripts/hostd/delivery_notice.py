"""Binding-owned, crash-safe notices for signed messages awaiting delivery.

This module deliberately has no RemoteDispatch/foreign-agent runtime dependency:
the only actor that may post or edit a notice is the selected local binding's
sync bot.  Relay events and native messages are read again from their real
boundaries; Store rows contain only immutable identifiers and hashes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import buzz_feishu_group_sync as gs
import recovery_authority as authority
from recovery_relay import RecoveryRelay, canonical_origin

from . import delivery_mapping, remote_approval
from .async_io import thread_call
from .bot_clients import BotLarkCli
from .safety import read_owned
from .signed_reads import SignedReader, _event_result_shape
from .store import Store

_HEX = re.compile(r"[0-9a-f]{64}")
_NOTICE_DELAY = 60
_MAX_ROWS = 128
_MAX_QUERY = 257
_NOTICE_PREFIX = "hostd-delivery-notice-v1"
_ERROR = ("本话题的签名消息仍待本机核验。怎么解决：检查当前频道、完整签名记录和同步 bot 读回。"
          "\n复制给 AI：帮我检查这个本话题的待投递状态；不要输出消息正文、图片、密钥或个人信息。")


class DeliveryNoticePending(ValueError):
    """Fail-closed internal result; its text never contains source data."""

    def __init__(self):
        super().__init__(_ERROR)


def _need(condition):
    if not condition:
        raise DeliveryNoticePending()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _event_id(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _json(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            _need(key not in out)
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _v: (_ for _ in ()).throw(ValueError()))


def _event_matches(event, filt):
    """Reject a whole signed packet if any row escaped the requested filter."""
    _need(_event_result_shape(event))
    for name, wanted in filt.items():
        if name == "limit":
            continue
        if name == "kinds":
            _need(event["kind"] in wanted)
        elif name == "authors":
            _need(event["pubkey"] in wanted)
        elif name == "ids":
            _need(event["id"] in wanted)
        elif name.startswith("#"):
            _need(any(tag[0] == name[1:] and len(tag) > 1 and tag[1] in wanted
                      for tag in event["tags"]))
        else:
            raise DeliveryNoticePending()


def _latest(events):
    return authority.latest(list(events))


def _profile_name(profile):
    body = _json(profile["content"])
    _need(type(body) is dict)
    name = body.get("name", body.get("display_name"))
    if type(name) is not str or not 0 < len(name) <= 128 or any(ord(c) < 32 for c in name):
        return None
    return name


def _display_name(value, forbidden=()):
    if (type(value) is not str or not 0 < len(value) <= 128
            or any(ord(char) < 32 for char in value) or value in forbidden
            or _HEX.fullmatch(value) or re.fullmatch(r"(?:cli|oc|ou)_[A-Za-z0-9_-]{6,}", value)):
        return None
    return value


def _card_doc(raw):
    _need(type(raw) is str and len(raw.encode("utf-8")) <= gs.MAX_CARD_BYTES)
    value = delivery_mapping.card_document(raw)
    _need(type(value) is dict and value.get("schema") != "2.0"
          and isinstance(value.get("elements"), list))
    return value


class DeliveryNoticeCoordinator:
    """One selected active binding's durable, per-source notice state machine."""

    def __init__(self, store, binding_id: str, round, *, utc_clock, monotonic_clock):
        _need(type(binding_id) is str and 0 < len(binding_id) <= 256)
        _need(callable(utc_clock) and callable(monotonic_clock))
        clients = getattr(round, "clients", None)
        _need(type(store) is Store and type(round) is delivery_mapping.MappedHostdRound
              and clients is not None and type(getattr(clients, "owner", None)) is BotLarkCli
              and getattr(round, "mapping_store", None) is store
              and getattr(round, "binding_id", None) == binding_id)
        self.store, self.binding_id, self.round = store, binding_id, round
        self.utc_clock, self.monotonic_clock = utc_clock, monotonic_clock
        self._closed = False
        self._active = set()
        self._idle = asyncio.Event()
        self._idle.set()
        self._close_waiter = None
        self._scan_lock = asyncio.Lock()
        self._scan_cursor = ""
        self.next_waiting_deadline = None
        self.retry_after_seconds = 5
        # Construction validates current local binding before accepting a hint.
        self._binding_snapshot()

    @property
    def scan_cursor(self):
        return self._scan_cursor

    @scan_cursor.setter
    def scan_cursor(self, value):
        _need(type(value) is str and (value == "" or _event_id(value)))
        self._scan_cursor = value

    def _binding_snapshot(self):
        with self.store._lock:
            self.store._delivery_notice_open()
            rows = [row for row in self.store.bindings() if row.get("binding_id") == self.binding_id]
        _need(len(rows) == 1)
        row = rows[0]
        _need(row.get("status") == "active")
        _need((row.get("channel_id"), row.get("chat_id"), row.get("sync_app_id")) ==
              (self.round.cfg.get("channel_id"), self.round.cfg.get("chat_id"), self.round.desk_app_id))
        _need(row.get("sync_app_id") == self.round.desk_app_id)
        return row

    @staticmethod
    def _binding_identity(row):
        return tuple(row.get(name) for name in ("binding_id", "channel_id", "chat_id", "sync_app_id",
            "config_path", "config_dir", "data_dir", "mirror_pubkey", "status"))

    def _config_snapshot(self, binding):
        path = Path(binding["config_path"])
        raw = read_owned(path)
        cfg = _json(raw)
        _need(type(cfg) is dict)
        required = ("channel_id", "chat_id", "desk_pubkey", "mirror_pubkey")
        _need(all(type(cfg.get(key)) is str and cfg.get(key) for key in required))
        _need(_event_id(getattr(self.round, "claim_relay_pubkey", None)))
        _need(tuple(cfg[key] for key in required) == tuple(self.round.cfg[key] for key in required))
        people = cfg.get("people_api")
        _need(type(people) is dict and
              (people.get("base_url"), people.get("signer_env_file")) ==
              (self.round.cfg["people_api"].get("base_url"),
               self.round.cfg["people_api"].get("signer_env_file")))
        _need(cfg["desk_pubkey"] in cfg.get("agents", {}))
        agent = cfg["agents"][cfg["desk_pubkey"]]
        _need(type(agent) is dict and agent.get("app_id") == binding["sync_app_id"]
              and agent.get("lark_config_dir") == binding.get("config_dir")
              and agent.get("lark_data_dir") == binding.get("data_dir"))
        mirror_env_path = cfg.get("mirror_env_file")
        _need(type(mirror_env_path) is str and mirror_env_path == self.round.cfg.get("mirror_env_file"))
        from .bot_round import parse_env
        mirror_env = parse_env(read_owned(mirror_env_path).decode("utf-8"))
        _need(canonical_origin(mirror_env.get("BUZZ_RELAY_URL", "")) == canonical_origin(self.round.clients.relay_url)
              and gs._signer_pubkey(gs.secret_hex(mirror_env.get("BUZZ_PRIVATE_KEY", ""), "mirror"))
                    == self.round.cfg["mirror_pubkey"]
              and mirror_env.get("BUZZ_AUTH_TAG", "") == getattr(self.round.clients, "mirror_auth_tag", ""))
        return cfg

    def _now(self):
        value = self.utc_clock()
        _need(type(value) is int and 0 <= value < 2**63)
        mono = self.monotonic_clock()
        _need(type(mono) in (int, float) and mono >= 0)
        return value

    async def _local_guard(self, expected=None):
        _need(not self._closed)
        binding = self._binding_snapshot()
        await thread_call(self._config_snapshot, binding)
        _need(self._binding_identity(self._binding_snapshot()) == self._binding_identity(binding))
        identity = await thread_call(self.round.clients.owner.identity)
        _need(self._binding_identity(self._binding_snapshot()) == self._binding_identity(binding))
        await thread_call(self._config_snapshot, binding)
        _need(self._binding_identity(self._binding_snapshot()) == self._binding_identity(binding))
        _need(identity == (binding["sync_app_id"], ""))
        _need(str(self.round.clients.owner.config_dir) == binding.get("config_dir")
              and str(self.round.clients.owner.data_dir) == binding.get("data_dir"))
        listing = await thread_call(self.round.clients.owner.member_listing, binding["chat_id"], "open_id")
        _need(self._binding_identity(self._binding_snapshot()) == self._binding_identity(binding))
        await thread_call(self._config_snapshot, binding)
        _need(self._binding_identity(self._binding_snapshot()) == self._binding_identity(binding))
        identity_after_members = await thread_call(self.round.clients.owner.identity)
        _need(self._binding_identity(self._binding_snapshot()) == self._binding_identity(binding))
        await thread_call(self._config_snapshot, binding)
        _need(self._binding_identity(self._binding_snapshot()) == self._binding_identity(binding))
        _need(identity_after_members == identity)
        _need(listing.complete and listing.bots.get(binding["sync_app_id"]) is not None)
        if expected is not None:
            row = self.store.get_delivery_notice(self.binding_id, expected.source_id)
            _need(row is not None and self._row_identity(row) == self._row_identity(expected))
            _need((row.channel_id, row.chat_id, row.sync_app_id) ==
                  (binding["channel_id"], binding["chat_id"], binding["sync_app_id"]))
        return binding, listing

    @staticmethod
    def _immutable(row):
        return tuple(getattr(row, name) for name in (
            "binding_id", "source_id", "source_author_pubkey", "source_app_id", "source_created_at",
            "sync_app_id", "channel_id", "chat_id", "root_message_id", "first_observed_at",
            "delay_seconds", "deadline_at", "notice_uuid", "notice_version", "notice_content_sha256"))

    @classmethod
    def _row_identity(cls, row):
        return cls._immutable(row) + (row.state, row.resolved_without_notice_at, row.notice_message_id,
            row.recovery_version, row.recovery_content_sha256, row.created_at, row.updated_at)

    def _signed_reader(self):
        cfg = self.round.cfg
        env_path = cfg.get("people_api", {}).get("signer_env_file")
        _need(type(env_path) is str)
        from .bot_round import parse_env
        raw = read_owned(env_path)
        values = parse_env(raw.decode("utf-8"))
        key = gs.secret_hex(values.get("BUZZ_PRIVATE_KEY", ""), "owner")
        _need(gs._signer_pubkey(key) == gs._signer_pubkey(self.round._signer()))
        pin = self.round.claim_relay_pubkey
        _need(type(pin) is str and _HEX.fullmatch(pin))
        relay = RecoveryRelay(self.round.clients.relay_url, key, pin,
                              http=self.round.clients.http,
                              now=lambda: datetime.fromtimestamp(self._now(), timezone.utc))
        return SignedReader(relay)

    async def _query(self, filt):
        _need(type(filt) is dict and 1 <= len(filt) <= 5)
        rows = await thread_call(self._query_sync, filt)
        _need(type(rows) is list and len(rows) < min(filt.get("limit", _MAX_QUERY), 256))
        ids = []
        for row in rows:
            _event_matches(row, filt)
            ids.append(row["id"])
        _need(len(ids) == len(set(ids)))
        # A bounded response at its cap cannot prove completeness.
        return rows

    def _query_sync(self, filt):
        reader = self._signed_reader()
        try:
            return asyncio.run(reader.read("query", filters=[filt]))
        except Exception:
            raise DeliveryNoticePending() from None

    async def _source_proof(self, source_id):
        _need(_event_id(source_id))
        rows = await self._query({"kinds": [9], "ids": [source_id],
                                  "#h": [self.round.cfg["channel_id"]], "limit": 2})
        _need(len(rows) == 1)
        event = rows[0]
        _need(event["id"] == source_id and event["kind"] == 9)
        remote_approval._event(event, 9, self._now())
        _need(delivery_mapping._verified(event, self.round.cfg["channel_id"]))
        await self._local_guard()
        channel = self.round.cfg["channel_id"]
        authority.exact_tag(event, "h", channel)
        root_event_id = authority.canonical_thread(event, channel)
        _need(_event_id(root_event_id))
        root_rows = await self._query({"kinds": [9], "ids": [root_event_id],
                                       "#h": [channel], "limit": 2})
        await self._local_guard()
        _need(len(root_rows) == 1)
        root_event = root_rows[0]
        _need(root_event["kind"] == 9 and root_event["id"] == root_event_id)
        remote_approval._event(root_event, 9, self._now())
        _need(delivery_mapping._verified(root_event, channel))
        authority.exact_tag(root_event, "h", channel)
        mids = authority.tags(root_event, "feishu")
        roots = authority.tags(root_event, "feishu-root")
        _need(len(mids) == 1 and len(mids[0]) == 2 and gs.MESSAGE_ID_RE.fullmatch(mids[0][1])
              and len(roots) == 1 and len(roots[0]) == 2 and gs.MESSAGE_ID_RE.fullmatch(roots[0][1])
              and mids[0][1] == roots[0][1])
        root_mid = mids[0][1]
        _need(root_event["pubkey"] == self.round.cfg["mirror_pubkey"])
        root_view = await thread_call(self.round.clients.owner.message_view, root_mid, "open_id")
        await self._local_guard()
        _need(isinstance(root_view, dict) and root_view.get("message_id") == root_mid
              and root_view.get("chat_id") == self.round.cfg["chat_id"]
              and root_view.get("deleted") is not True
              and (root_view.get("root_id") or root_view.get("message_id")) == root_mid)

        # Current public owner attestation and app policy, read from A's pinned relay.
        profiles = await self._query({"kinds": [0], "authors": [event["pubkey"]], "limit": _MAX_QUERY})
        await self._local_guard()
        profile = _latest(profiles)
        _need(profile is not None and profile["pubkey"] == event["pubkey"])
        remote_approval._event(profile, 0, self._now())
        owner = remote_approval._owner(profile, event["pubkey"], self._now())
        _need(owner != event["pubkey"])
        policies = await self._query({"kinds": [30177], "authors": [owner], "#d": [event["pubkey"]], "limit": _MAX_QUERY})
        await self._local_guard()
        policy = _latest(policies)
        _need(policy is not None and policy["pubkey"] == owner)
        policy_body = authority.policy_body(policy, event["pubkey"])
        remote_approval._event(policy, 30177, self._now())
        feishu = policy_body.get("feishu")
        _need(type(feishu) is dict and feishu.get("mirror") is not True
              and gs.APP_ID_RE.fullmatch(feishu.get("app_id", "")))
        app_id = feishu["app_id"]

        rosters = await self._query({"kinds": [39002], "authors": [self.round.claim_relay_pubkey],
                                     "#d": [channel], "limit": _MAX_QUERY})
        await self._local_guard()
        roster = _latest(rosters)
        _need(roster is not None and roster["pubkey"] == self.round.claim_relay_pubkey)
        remote_approval._event(roster, 39002, self._now())
        roles = authority.membership(roster, channel)
        _need(roles.get(event["pubkey"]) == "bot")
        binding, listing = await self._local_guard()
        _need(listing.bots.get(app_id) is not None)

        # Validate every row in the bounded author/channel/source edit packet;
        # the transport filter narrows retrieval but never replaces validation.
        edits = await self._query({"kinds": [40003], "authors": [event["pubkey"]],
                                   "#e": [source_id], "#h": [channel], "limit": _MAX_QUERY})
        await self._local_guard()
        selected = []
        for edit in edits:
            refs = [tag for tag in edit["tags"] if tag and tag[0] == "e"]
            hs = [tag for tag in edit["tags"] if tag and tag[0] == "h"]
            if any(len(tag) >= 2 and tag[1] == source_id for tag in refs):
                _need(edit["kind"] == 40003 and edit["pubkey"] == event["pubkey"]
                      and len(refs) == 1 and refs[0] == ["e", source_id]
                      and len(hs) == 1 and hs[0] == ["h", channel]
                      and not any(tag[0] == "imeta" for tag in edit["tags"]))
                selected.append(edit)
        _need(len(selected) < _MAX_QUERY)
        for edit in selected:
            remote_approval._event(edit, 40003, self._now())
        latest_edit = max(selected, key=lambda item: (item["created_at"], item["id"]), default=None)
        text = event["content"] if latest_edit is None else latest_edit["content"]
        source_images = self._source_images(event)
        # The finite schema supports text-only overlays. An edit that mutates
        # resource metadata remains pending instead of borrowing old media.
        for edit in selected:
            _need(not any(tag and tag[0] == "imeta" for tag in edit["tags"]))
        await self._local_guard()
        return {"event": event, "root_event": root_event, "root_mid": root_mid,
                "profile": profile, "name": _display_name(_profile_name(profile), (event["pubkey"], app_id)), "owner": owner,
                "policy": policy, "app_id": app_id, "roster": roster,
                "relay_pin": self.round.claim_relay_pubkey,
                "edits": tuple(selected), "text": text, "images": source_images,
                "binding": binding}

    def _source_images(self, event):
        rows = [tag for tag in event["tags"] if tag and tag[0] == "imeta"]
        _need(len(rows) <= 4)
        answer = []
        for row in rows:
            _need(len(row) == 5)
            values = {}
            for field in row[1:]:
                key, sep, value = field.partition(" ")
                _need(sep and key in ("url", "m", "x", "size") and key not in values and value)
                values[key] = value
            _need(set(values) == {"url", "m", "x", "size"})
            parsed = urlsplit(values["url"])
            relay = urlsplit(self.round.clients.relay_url)
            _need(parsed.scheme == relay.scheme and parsed.netloc == relay.netloc and not parsed.query and not parsed.fragment)
            _need(parsed.path.startswith("/media/") and parsed.path.count("/") == 2)
            digest = values["x"]
            size = values["size"]
            mime = values["m"]
            _need(_event_id(digest) and mime in ("image/png", "image/jpeg")
                  and re.fullmatch(r"[1-9][0-9]{0,7}", size) and int(size) <= 10_000_000)
            name = parsed.path.rsplit("/", 1)[1]
            _need(parsed.username is None and parsed.password is None
                  and gs.MEDIA_SEGMENT_RE.fullmatch(name)
                  and name.split(".", 1)[0] == digest
                  and name.endswith(".png" if mime == "image/png" else ".jpg"))
            answer.append((digest, mime, int(size)))
        return tuple(answer)

    def _notice_card(self, row, name, group_name, source_event, *, recovered=False):
        version = 1 if recovered else row.notice_version
        title = "本话题已恢复" if recovered else "本话题等待投递"
        group = group_name if group_name else "本群"
        agent = name if name else "远端助手"
        if recovered:
            text = (f"{agent} 的签名消息已在 {group} 的本话题中读回确认。此提示已恢复，"
                    "不要重复发送原消息。怎么解决：继续在本话题查看原消息。"
                    "\n复制给 AI：帮我确认本话题中的原消息和恢复提示；不要粘贴密钥或私人内容。")
        else:
            text = (f"{agent} 的签名消息暂未确认送达。请在 {group} 的本话题中检查同步 bot 与频道连接，"
                    "不要重复发送原消息。60 秒后本机只提示一次；五分钟后如仍未恢复，请在控制台检查连接状态。"
                    "怎么解决：恢复 agent 与 Relay 后检查本话题。"
                    "\n复制给 AI：帮我检查这个本话题的待投递状态；不要粘贴密钥或私人内容。")
        link = gs.open_link(self.round.cfg["people_api"]["base_url"], source_event["id"],
                            row.channel_id, gs.buzz_thread_root(source_event))
        _need(type(link) is str and link.startswith("https://"))
        marker = f"{_NOTICE_PREFIX}:{row.notice_uuid}:{version}"
        # The marker lives in a supported Card 1.0 markdown link fragment. It
        # remains durable in the native card JSON but is not shown as prose.
        footer = f"[在 Buzz 查看原消息]({link}#{marker})"
        card = {"config": {"wide_screen_mode": True, "update_multi": True},
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": gs.card_markdown(text)}},
                    {"tag": "note", "elements": [{"tag": "lark_md", "content": footer}]}]}
        raw = json.dumps(card, ensure_ascii=False, separators=(",", ":"))
        _need(len(raw.encode("utf-8")) < gs.MAX_CARD_BYTES)
        return raw, version, _sha(raw.encode("utf-8"))

    async def _group_name(self, chat_id):
        try:
            value = await thread_call(self.round.clients.owner.chat, chat_id, "open_id")
            name = value.get("name") if isinstance(value, dict) else None
            return _display_name(name, (chat_id, self.round.desk_app_id))
        except Exception:
            return None

    async def _expected_card(self, proof, row, *, recovered=False):
        group = await self._group_name(row.chat_id)
        await self._local_guard(row)
        raw, version, digest = self._notice_card(row, proof["name"], group, proof["event"],
                                                 recovered=recovered)
        expected_digest = row.recovery_content_sha256 if recovered else row.notice_content_sha256
        if expected_digest and digest != expected_digest and not recovered:
            # Notices created before display_name support used the anonymous
            # label. Reproduce only that exact stored intent; recovery gets
            # the current attested name. The original card is never reposted.
            raw, version, digest = self._notice_card(row, None, group, proof["event"])
        if expected_digest:
            _need(digest == expected_digest)
        return raw, version, digest

    def _card_has_status(self, raw, row, version):
        doc = _card_doc(raw)
        marker = f"{_NOTICE_PREFIX}:{row.notice_uuid}:{version}"
        values = []
        for element in doc["elements"]:
            if isinstance(element, dict) and element.get("tag") == "note":
                for child in element.get("elements", []):
                    if isinstance(child, dict) and child.get("tag") == "lark_md":
                        values.append(child.get("content"))
        return (len(values) == 1 and type(values[0]) is str
                and values[0].startswith("[在 Buzz 查看原消息](https://")
                and values[0].endswith(f"#{marker})"))

    def _native_exact(self, message, row, card, version, digest):
        if not isinstance(message, dict):
            return False
        try:
            body = message.get("body")
            raw = body.get("content") if isinstance(body, dict) else message.get("content")
            sender = message.get("sender") or {}
            return (message.get("message_id") and message.get("chat_id") == row.chat_id
                    and message.get("deleted") is not True
                    and (message.get("root_id") or message.get("thread_id") or message.get("message_id")) == row.root_message_id
                    and message.get("msg_type") == "interactive"
                    and sender.get("sender_type") == "app" and sender.get("id_type") == "app_id"
                    and sender.get("id") == row.sync_app_id and type(raw) is str
                    and _sha(card.encode("utf-8")) == digest and _card_doc(raw) == _card_doc(card)
                    and self._card_has_status(raw, row, version))
        except Exception:
            return False

    def _notice_marker_candidates(self, messages, row):
        candidates = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            body = message.get("body")
            raw = body.get("content") if isinstance(body, dict) else message.get("content")
            sender = message.get("sender") or {}
            if (message.get("chat_id") == row.chat_id
                    and (message.get("root_id") or message.get("thread_id") or message.get("message_id")) == row.root_message_id
                    and sender.get("sender_type") == "app" and sender.get("id_type") == "app_id"
                    and sender.get("id") == row.sync_app_id
                    and type(raw) is str and row.notice_uuid in raw):
                candidates.append(message)
        return candidates

    async def _exact_notice_get(self, row, proof, mid, *, recovered=False):
        card, version, digest = await self._expected_card(proof, row, recovered=recovered)
        await self._local_guard(row)
        message = await thread_call(self.round.clients.owner.message_view, mid, "open_id")
        await self._revalidate(proof, row)
        _need(self._native_exact(message, row, card, version, digest))
        return card, version, digest, message

    async def _thread_delivery(self, row, proof):
        rows, partial = await thread_call(self.round.clients.owner.thread_messages, row.root_message_id)
        proof = await self._revalidate(proof, row)
        _need(not partial and isinstance(rows, list) and len(rows) <= 1000)
        source = proof["event"]
        mapped = []
        for message in rows:
            _need(isinstance(message, dict))
            mid = message.get("message_id")
            _need(type(mid) is str and gs.MESSAGE_ID_RE.fullmatch(mid)
                  and message.get("chat_id") == row.chat_id)
            actual_root = message.get("root_id") or message.get("thread_id") or message.get("message_id")
            if actual_root != row.root_message_id and mid != row.root_message_id:
                continue
            body = message.get("body")
            raw = body.get("content") if isinstance(body, dict) else message.get("content")
            if type(raw) is not str:
                continue
            link = delivery_mapping._receipt_link(message)
            if type(link) is not str:
                continue
            query = parse_qs(urlsplit(link).query, strict_parsing=True)
            if query.get("e") != [source["id"]] or query.get("c") != [row.channel_id]:
                continue
            if query.get("t", [None])[0] != gs.buzz_thread_root(source):
                continue
            mapped.append(message)
        # A full history row is only a candidate. Duplicate mapping candidates,
        # even if one body looks right, cannot establish a unique delivery.
        _need(len(mapped) <= 1)
        if not mapped:
            return None
        candidate = mapped[0]
        message = await thread_call(self.round.clients.owner.message_view,
                                    candidate["message_id"], "open_id")
        proof = await self._revalidate(proof, row)
        _need(isinstance(message, dict) and message.get("message_id") == candidate["message_id"]
              and message.get("deleted") is not True
              and message.get("chat_id") == candidate.get("chat_id")
              and message.get("root_id") == candidate.get("root_id")
              and message.get("thread_id") == candidate.get("thread_id")
              and tuple((message.get("sender") or {}).get(k) for k in ("id", "id_type", "sender_type"))
              == tuple((candidate.get("sender") or {}).get(k) for k in ("id", "id_type", "sender_type"))
              and message.get("msg_type") == candidate.get("msg_type"))
        _need((message.get("root_id") or message.get("thread_id") or message.get("message_id"))
              == row.root_message_id)
        body = message.get("body")
        raw = body.get("content") if isinstance(body, dict) else message.get("content")
        _need(type(raw) is str)
        sender = message.get("sender") or {}
        _need(sender.get("sender_type") == "app" and sender.get("id_type") == "app_id"
              and sender.get("id") == proof["app_id"])
        create_time = message.get("create_time")
        _need(type(create_time) is str and re.fullmatch(r"[0-9]{1,16}", create_time)
              and 0 < int(create_time) <= self._now() * 1000)
        expected = delivery_mapping.message_card(source, proof["name"], proof["text"],
            self.round.cfg["people_api"]["base_url"], row.channel_id)
        expected_doc = _card_doc(expected)
        doc = _card_doc(raw)
        image_elements = [element for element in doc["elements"] if isinstance(element, dict) and element.get("tag") == "img"]
        _need(len(image_elements) == len(proof["images"]))
        keys = []
        for element in image_elements:
            key = element.get("img_key")
            _need(type(key) is str and gs.FEISHU_IMAGE_KEY_RE.fullmatch(key))
            keys.append(key)
        _need(len(keys) == len(set(keys)))
        expected_with_images = dict(expected_doc)
        expected_with_images["elements"] = list(expected_doc["elements"])
        expected_with_images["elements"][-1:-1] = [
            {"tag": "img", "img_key": key, "alt": {"tag": "plain_text", "content": ""}}
            for key in keys]
        _need(doc == expected_with_images)
        for key, (digest, mime, size) in zip(keys, proof["images"]):
            result = await thread_call(self._download_image, message["message_id"], key)
            proof = await self._revalidate(proof, row)
            actual_mime, actual_size, actual_digest = result
            _need((actual_mime, actual_size, actual_digest) == (mime, size, digest))
        # A second current signed read closes races during media/native reads.
        latest = await self._source_proof(row.source_id)
        _need(self._proof_identity(proof) == self._proof_identity(latest))
        return message

    @staticmethod
    def _delivery_identity(message):
        _need(isinstance(message, dict))
        body = message.get("body")
        raw = body.get("content") if isinstance(body, dict) else message.get("content")
        _need(type(raw) is str)
        sender = message.get("sender") or {}
        return (message.get("message_id"), message.get("chat_id"),
                message.get("root_id"), message.get("thread_id"),
                (sender.get("sender_type"), sender.get("id_type"), sender.get("id")),
                message.get("msg_type"),
                message.get("create_time"), _sha(raw.encode("utf-8")))

    async def _same_delivery(self, row, proof, original_identity):
        """Re-prove the complete original native receipt after notice IO."""
        current = await self._thread_delivery(row, proof)
        _need(current is not None and self._delivery_identity(current) == original_identity)
        return current

    def _download_image(self, mid, key):
        with tempfile.TemporaryDirectory(prefix="hostd-delivery-image-") as name:
            directory = Path(name)
            path = self.round.clients.owner.download_image(mid, key, directory)
            _need(path.parent == directory and len(list(directory.iterdir())) == 1)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                metadata = os.fstat(fd)
                _need(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.geteuid())
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            raw = read_owned(path, max_bytes=10_000_000)
            _need(1 <= len(raw) <= 10_000_000)
            if raw.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9"):
                mime = "image/jpeg"
            else:
                raise DeliveryNoticePending()
            return mime, len(raw), _sha(raw)

    @staticmethod
    def _proof_identity(proof):
        return (proof["event"]["id"], proof["event"]["content"],
                tuple((edit["id"], edit["content"]) for edit in proof["edits"]),
                proof["app_id"], proof["root_mid"], proof["images"], proof["profile"]["id"],
                proof["policy"]["id"], proof["roster"]["id"], proof["relay_pin"])

    async def _revalidate(self, proof, row=None):
        current = await self._source_proof(proof["event"]["id"])
        _need(self._proof_identity(proof) == self._proof_identity(current))
        if row is not None:
            await self._local_guard(row)
        return current

    def _rows_for_binding(self, limit=_MAX_ROWS):
        _need(type(limit) is int and 1 <= limit <= _MAX_ROWS)
        states = ("waiting", "reserved", "unknown", "noticed", "recovery_reserved", "recovery_unknown")
        placeholders = ",".join("?" for _ in states)
        with self.store._lock:
            self.store._delivery_notice_open()
            values = self.store.conn.execute(
                f"SELECT * FROM delivery_notice WHERE binding_id=? AND state IN ({placeholders}) "
                "AND source_id>? ORDER BY source_id LIMIT ?",
                (self.binding_id, *states, self._scan_cursor, limit)).fetchall()
            if not values:
                self._scan_cursor = ""
                values = self.store.conn.execute(
                    f"SELECT * FROM delivery_notice WHERE binding_id=? AND state IN ({placeholders}) "
                    "ORDER BY source_id LIMIT ?", (self.binding_id, *states, limit)).fetchall()
            rows = tuple(self.store._delivery_notice_record(value) for value in values)
            deadline = self.store.conn.execute(
                "SELECT min(deadline_at) FROM delivery_notice WHERE binding_id=? AND state='waiting'",
                (self.binding_id,)).fetchone()[0]
        self._scan_cursor = rows[-1].source_id if rows else ""
        self.next_waiting_deadline = deadline
        return rows

    def _enter(self):
        _need(not self._closed)
        task = asyncio.current_task()
        self._active.add(task)
        self._idle.clear()
        return task

    def _leave(self, task):
        self._active.discard(task)
        if not self._active:
            self._idle.set()

    async def exclude_candidate(self, source_id):
        """Only complete signed non-candidates may leave the untrusted queue.

        A missing/invalid source, roster or scope raises and remains retryable.
        This never changes an existing notice or authorizes a native effect.
        """
        task = self._enter()
        try:
            _need(_event_id(source_id))
            channel = self.round.cfg['channel_id']
            rows = await self._query({'kinds': [9], 'ids': [source_id], '#h': [channel], 'limit': 2})
            _need(len(rows) == 1)
            event = rows[0]
            remote_approval._event(event, 9, self._now())
            _need(delivery_mapping._verified(event, channel))
            await self._local_guard()
            if authority.canonical_thread(event, channel) == source_id or event['pubkey'] == self.round.cfg['mirror_pubkey']:
                return True
            rows = await self._query({'kinds': [39002], 'authors': [self.round.claim_relay_pubkey],
                                      '#d': [channel], 'limit': _MAX_QUERY})
            roster = _latest(rows)
            _need(roster is not None)
            remote_approval._event(roster, 39002, self._now())
            roles = authority.membership(roster, channel)
            await self._local_guard()
            return roles.get(event['pubkey']) in ('owner', 'admin', 'member')
        finally:
            self._leave(task)

    async def observe(self, source_id: str):
        task = self._enter()
        try:
            proof = await self._source_proof(source_id)
            proof = await self._revalidate(proof)
            await self._local_guard()
            old = self.store.get_delivery_notice(self.binding_id, source_id)
            if old is not None:
                _need(old.source_author_pubkey == proof["event"]["pubkey"]
                      and old.source_app_id == proof["app_id"]
                      and old.root_message_id == proof["root_mid"])
                return old
            notice_uuid = str(uuid.uuid4())
            provisional = type("Notice", (), {})()
            provisional.notice_uuid = notice_uuid
            provisional.notice_version = 1
            provisional.notice_content_sha256 = ""
            provisional.channel_id = proof["binding"]["channel_id"]
            group_name = await self._group_name(self.round.cfg["chat_id"])
            await self._local_guard()
            proof = await self._revalidate(proof)
            now = self._now()
            card, version, digest = self._notice_card(provisional, proof["name"], group_name,
                                                       proof["event"])
            record, _created = self.store.observe_delivery_notice(
                self.binding_id, source_id, source_author_pubkey=proof["event"]["pubkey"],
                source_app_id=proof["app_id"], source_created_at=proof["event"]["created_at"],
                sync_app_id=proof["binding"]["sync_app_id"], channel_id=proof["binding"]["channel_id"],
                chat_id=proof["binding"]["chat_id"], root_message_id=proof["root_mid"],
                first_observed_at=now, delay_seconds=_NOTICE_DELAY, deadline_at=now + _NOTICE_DELAY,
                notice_uuid=notice_uuid, notice_version=1, notice_content_sha256=digest)
            return record
        except Exception:
            return None
        finally:
            self._leave(task)

    async def _process(self, row):
        try:
            proof = await self._source_proof(row.source_id)
            await self._local_guard(row)
            _need((proof["event"]["pubkey"], proof["app_id"], proof["root_mid"]) ==
                  (row.source_author_pubkey, row.source_app_id, row.root_message_id))
            delivered = await self._thread_delivery(row, proof)
            delivery_identity = self._delivery_identity(delivered) if delivered is not None else None
            await self._local_guard(row)
            proof = await self._revalidate(proof, row)
            now = self._now()
            if row.state == "waiting":
                if delivered is not None:
                    self.store.resolve_delivery_notice_without_notice(self.binding_id, row.source_id,
                        expected_state="waiting", observed_delivery_at=now, now=now)
                    return
                if now < row.deadline_at:
                    return
                card, version, digest = await self._expected_card(proof, row)
                _need(version == row.notice_version and digest == row.notice_content_sha256)
                proof = await self._revalidate(proof, row)
                reserved = self.store.reserve_delivery_notice_send(self.binding_id, row.source_id,
                    expected_state="waiting", now=now)
                _need(reserved.state == "reserved" and self._immutable(reserved) == self._immutable(row))
                row = self.store.get_delivery_notice(self.binding_id, row.source_id)
                _need(row is not None and self._row_identity(row) == self._row_identity(reserved))
                # Store intent is committed before the one native mutation.
                proof = await self._revalidate(proof, row)
                try:
                    mid = await thread_call(self.round.clients.owner.reply_card,
                                            row.root_message_id, card, row.notice_uuid)
                except Exception:
                    self.store.mark_delivery_notice_unknown(self.binding_id, row.source_id,
                        expected_state="reserved", now=self._now())
                    return
                proof = await self._revalidate(proof, row)
                try:
                    _card, v, h, _message = await self._exact_notice_get(row, proof, mid)
                    self.store.record_delivery_notice_readback(self.binding_id, row.source_id,
                        expected_state="reserved", notice_message_id=mid,
                        observed_notice_version=v, observed_content_sha256=h, now=self._now())
                except Exception:
                    self.store.mark_delivery_notice_unknown(self.binding_id, row.source_id,
                        expected_state="reserved", now=self._now())
                return
            if row.state in ("reserved", "unknown"):
                if row.notice_message_id:
                    try:
                        _card, version, digest, _message = await self._exact_notice_get(row, proof, row.notice_message_id)
                        self.store.record_delivery_notice_readback(self.binding_id, row.source_id,
                            expected_state=row.state, notice_message_id=row.notice_message_id,
                            observed_notice_version=version, observed_content_sha256=digest, now=self._now())
                    except Exception:
                        pass
                    return
                history, partial = await thread_call(self.round.clients.owner.thread_messages, row.root_message_id)
                proof = await self._revalidate(proof, row)
                if partial or not isinstance(history, list) or len(history) > 1000:
                    if row.state == "reserved":
                        self.store.mark_delivery_notice_unknown(self.binding_id, row.source_id,
                            expected_state="reserved", now=self._now())
                    return
                card, version, digest = await self._expected_card(proof, row)
                proof = await self._revalidate(proof, row)
                markers = self._notice_marker_candidates(history, row)
                if len(markers) != 1 or not self._native_exact(markers[0], row, card, version, digest):
                    if row.state == "reserved":
                        self.store.mark_delivery_notice_unknown(self.binding_id, row.source_id,
                            expected_state="reserved", now=self._now())
                    return
                mid = markers[0].get("message_id")
                try:
                    _card, version, digest, _message = await self._exact_notice_get(row, proof, mid)
                    self.store.record_delivery_notice_readback(self.binding_id, row.source_id,
                        expected_state=row.state, notice_message_id=mid,
                        observed_notice_version=version, observed_content_sha256=digest, now=self._now())
                except Exception:
                    if row.state == "reserved":
                        self.store.mark_delivery_notice_unknown(self.binding_id, row.source_id,
                            expected_state="reserved", now=self._now())
                return
            if row.state in ("recovery_reserved", "recovery_unknown"):
                if row.notice_message_id is None or row.recovery_version is None:
                    return
                if delivered is None:
                    if row.state == "recovery_reserved":
                        self.store.mark_delivery_notice_unknown(self.binding_id, row.source_id,
                            expected_state="recovery_reserved", now=self._now())
                    return
                try:
                    card, version, digest, _message = await self._exact_notice_get(
                        row, proof, row.notice_message_id, recovered=True)
                    await self._same_delivery(row, proof, delivery_identity)
                    current = self.store.get_delivery_notice(self.binding_id, row.source_id)
                    _need(current is not None and self._row_identity(current) == self._row_identity(row))
                    self.store.record_delivery_notice_recovery_readback(self.binding_id, row.source_id,
                        expected_state=row.state, notice_message_id=row.notice_message_id,
                        observed_recovery_version=version, observed_content_sha256=digest, now=self._now())
                except Exception:
                    if row.state == "recovery_reserved":
                        self.store.mark_delivery_notice_unknown(self.binding_id, row.source_id,
                            expected_state="recovery_reserved", now=self._now())
                return
            if row.state == "noticed":
                if delivered is None or row.notice_message_id is None:
                    return
                card, version, digest = await self._expected_card(proof, row, recovered=True)
                proof = await self._revalidate(proof, row)
                reserved = self.store.reserve_delivery_notice_recovery(self.binding_id, row.source_id,
                    expected_state="noticed", notice_message_id=row.notice_message_id,
                    recovery_version=version, recovery_content_sha256=digest, now=self._now())
                _need(reserved.state == "recovery_reserved" and self._immutable(reserved) == self._immutable(row)
                      and reserved.notice_message_id == row.notice_message_id
                      and reserved.recovery_version == version and reserved.recovery_content_sha256 == digest)
                row = self.store.get_delivery_notice(self.binding_id, row.source_id)
                _need(row is not None and self._row_identity(row) == self._row_identity(reserved))
                proof = await self._revalidate(proof, row)
                await self._same_delivery(row, proof, delivery_identity)
                current = self.store.get_delivery_notice(self.binding_id, row.source_id)
                _need(current is not None and self._row_identity(current) == self._row_identity(reserved))
                try:
                    await thread_call(self.round.clients.owner.update_card, row.notice_message_id, card)
                except Exception:
                    self.store.mark_delivery_notice_unknown(self.binding_id, row.source_id,
                        expected_state="recovery_reserved", now=self._now())
                    return
                try:
                    _card, version, digest, _message = await self._exact_notice_get(
                        row, proof, row.notice_message_id, recovered=True)
                    await self._same_delivery(row, proof, delivery_identity)
                    current = self.store.get_delivery_notice(self.binding_id, row.source_id)
                    _need(current is not None and self._row_identity(current) == self._row_identity(reserved))
                    self.store.record_delivery_notice_recovery_readback(self.binding_id, row.source_id,
                        expected_state="recovery_reserved", notice_message_id=row.notice_message_id,
                        observed_recovery_version=version, observed_content_sha256=digest, now=self._now())
                except Exception:
                    self.store.mark_delivery_notice_unknown(self.binding_id, row.source_id,
                        expected_state="recovery_reserved", now=self._now())
        except Exception:
            # A malformed, partial, stale or unavailable proof is only pending.
            return

    async def scan_once(self, *, limit=_MAX_ROWS) -> None:
        task = self._enter()
        try:
            async with self._scan_lock:
                await self._local_guard()
                for row in self._rows_for_binding(limit):
                    if self._closed:
                        return
                    await self._process(row)
        finally:
            self._leave(task)

    async def close(self) -> None:
        current = asyncio.current_task()
        _need(current not in self._active)
        if self._closed and not self._active:
            return
        self._closed = True
        # Retain and shield the original waiter: repeated cancellation of the
        # close caller must not abandon active native IO or let its owner close
        # shared Store/client resources before that IO has joined.
        if self._close_waiter is None:
            self._close_waiter = asyncio.create_task(self._idle.wait())
        waiter = self._close_waiter
        cancelled = False
        while not waiter.done():
            try:
                await asyncio.shield(waiter)
            except asyncio.CancelledError:
                cancelled = True
            except BaseException:
                break
        try:
            waiter.result()
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        if cancelled:
            raise asyncio.CancelledError
