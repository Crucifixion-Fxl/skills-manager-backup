"""Own-agent remote cards and reactions with durable uncertainty recovery.

This adapter consumes an existing explicit grant. Discovery and message mapping
are never authorization. The authorize callback is a trusted main-loop runtime
interface, not an HTTP or event payload. Bodies remain in memory only.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import time
import weakref

import buzz_feishu_group_sync as gs
from .agent_catalog import AgentRecord
from .agent_signed_reads import OwnAgentReader
from .async_io import thread_call
from .bot_clients import BotLarkCli, OwnImage
from .delivery_mapping import _verified, message_card
from .remote_mapping import RemoteMappingContext
from .safety import read_owned
from .store import RemoteGrantEvidence, Store

NOTICE = ('远端消息保持待核验，尚未确认投递。怎么解决：核查自己的应用、受保护配置、当前明确批准、完整消息读回与原话题；结果未知时先核验原投递，不要重复发送。'
          '\n复制给 AI：帮我核查 hostd 远端消息账本、当前签名授权和自己的 bot 回执；不要输出消息正文或凭据，不要把目标发现当作批准。')
_LOCKS = weakref.WeakKeyDictionary()
MAX_HISTORY = 150
MAX_EDITS = 256
PATCH_WINDOW_SECONDS = 14 * 24 * 60 * 60
MAX_PATCH_CONTENT_BYTES = 30 * 1024


class _Pending(ValueError):
    def __init__(self, reason): self.reason = reason


@dataclass(frozen=True)
class OutletOutcome:
    status: str
    reason: str = ''
    source_id: str = ''
    message_id: str = ''
    notice: str = ''

    def readback(self): return {**asdict(self), 'live_verified': False}


@dataclass(frozen=True)
class DrainOutcome:
    status: str
    reason: str = ''
    acked_count: int = 0
    pending_count: int = 0
    scanned_until: int = 0
    notice: str = ''

    def readback(self): return {**asdict(self), 'live_verified': False}


@dataclass(frozen=True)
class _ImageMetadata:
    url: str = field(repr=False)
    digest: str
    mime: str
    size: int


class RemoteOutlet:
    def __init__(self, store, record, reader, bot, mappings, *, authorize,
                 link_base, clock=lambda: int(time.time())):
        self.store, self.record, self.reader, self.bot = store, record, reader, bot
        self.mappings, self.authorize = mappings, authorize
        self.link_base, self.clock = link_base, clock

    def _lock(self):
        # The app/agent serialization spans targets and adapter instances, and
        # is local to its owning event loop (SQLite is never moved to a worker).
        locks = _LOCKS.setdefault(asyncio.get_running_loop(), {})
        return locks.setdefault(getattr(self.record, 'pubkey', None), asyncio.Lock())

    def _identity(self, grant):
        r, t = self.record, self.mappings.target
        e = grant.evidence
        if self.link_base != self.mappings.link_base:
            raise _Pending('identity')
        # Admit the same explicitly reviewed public origin on both adapters,
        # using the mapping boundary's strict host/port/path validation before
        # any signed-reader, profile or bot transport is opened.
        p = self.mappings._link_origin()
        if (not isinstance(self.store, Store) or not isinstance(r, AgentRecord)
                or not isinstance(self.reader, OwnAgentReader) or not isinstance(self.bot, BotLarkCli)
                or not isinstance(self.mappings, RemoteMappingContext)
                or self.mappings.reader is not self.reader or self.mappings.bot is not self.bot
                or r.status != 'own_bot_verified' or r.local_bot_verified is not True
                or e.channel_id not in r.channels
                or (r.pubkey, r.owner_pubkey, r.app_id) != (e.agent_id, e.owner_pubkey, e.app_id)
                or (self.reader.agent, self.reader.owner, self.reader.origin)
                    != (r.pubkey, r.owner_pubkey, e.relay_origin)
                or Path(self.reader.env_file) != r.env_file
                or (self.bot.app_id, self.bot.config_dir, self.bot.data_dir)
                    != (r.app_id, r.lark_config_dir, r.lark_data_dir)
                or self.bot.chat_id != e.chat_id
                or (t.agent_pubkey, t.owner_pubkey, t.app_id, t.channel_id, t.chat_id, t.chat_ref,
                    t.relay_url, t.mirror_pubkey, t.mirror_owner_pubkey, t.claimed_at)
                    != (e.agent_id, e.owner_pubkey, e.app_id, e.channel_id, e.chat_id, e.chat_ref,
                        e.relay_origin, e.mirror_pubkey, e.mirror_owner_pubkey, e.claimed_at)
                or p.scheme != 'https' or not p.hostname or p.username is not None
                or p.password is not None or p.path or p.query or p.fragment
                or any(ord(c) <= 32 or ord(c) == 127 for c in self.link_base)):
            raise _Pending('identity')

    def _files(self):
        r = self.record
        if (hashlib.sha256(read_owned(r.env_file, max_bytes=65536)).hexdigest() != r.env_sha256
                or hashlib.sha256(read_owned(r.lark_config_dir / 'config.json')).hexdigest() != r.profile_sha256):
            raise _Pending('identity')

    def _files_identity(self):
        self._files()
        if self.bot.identity()[0] != self.record.app_id:
            raise _Pending('identity')
        self._files()

    async def _fresh(self, grant):
        self._identity(grant)
        await thread_call(self._files)
        if (await thread_call(self.bot.identity))[0] != self.record.app_id:
            raise _Pending('identity')
        evidence = await self.authorize(self.record, grant.evidence.channel_id)
        # The trusted verifier may await IO. Revalidate protected snapshots and
        # actual own-app identity after that await, then perform only pure
        # scope/SQL CAS before dispatch or ACK. This is a bounded readback gate,
        # not a claim of atomicity against arbitrary external filesystem writes.
        await thread_call(self._files_identity)
        # Freshness renewal is explicit and can never replace the original pin.
        self._identity(grant)
        if (not isinstance(evidence, RemoteGrantEvidence)
                or not self.store.refresh_remote_proof(grant.target_id, evidence,
                    revision=grant.revision, scope_hash=grant.scope_hash, now=self.clock())):
            raise _Pending('authorization')

    async def _source(self, event, grant):
        channel = grant.evidence.channel_id
        if (not isinstance(event, dict) or event.get('pubkey') != self.record.pubkey
                or event.get('kind') != 9 or not await thread_call(_verified, event, channel)):
            raise _Pending('source')
        rows = await self.reader.query([{'kinds': [9], 'authors': [self.record.pubkey],
            'ids': [event['id']], '#h': [channel], 'limit': 2}])
        if len(rows) != 1 or rows[0] != event:
            raise _Pending('source')
        return rows[0]  # Own verified response snapshot, never caller-mutable data.

    def _image_metadata(self, event):
        tags = [tag for tag in event['tags'] if tag[0] == 'imeta']
        if not tags:
            return ()
        if len(tags) > 4:
            raise _Pending('unsupported')
        result, digests, urls = [], set(), set()
        for tag in tags:
            if len(tag) != 5:
                raise _Pending('source')
            fields = {}
            for raw in tag[1:]:
                name, separator, value = raw.partition(' ')
                if (separator != ' ' or name not in ('url', 'm', 'x', 'size') or name in fields
                        or not value or value != value.strip() or len(value) > 8192
                        or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
                    raise _Pending('source')
                fields[name] = value
            if (set(fields) != {'url', 'm', 'x', 'size'} or fields['m'] not in ('image/png', 'image/jpeg')
                    or not gs.HEX64_RE.fullmatch(fields['x'])
                    or not re.fullmatch(r'[1-9][0-9]{0,7}', fields['size'])):
                raise _Pending('source')
            size = int(fields['size'])
            suffix = '.png' if fields['m'] == 'image/png' else '.jpg'
            expected = self.reader.origin + '/media/' + fields['x'] + suffix
            if (size > 10_000_000 or fields['url'] != expected
                    or fields['x'] in digests or expected in urls):
                raise _Pending('source')
            digests.add(fields['x']); urls.add(expected)
            result.append(_ImageMetadata(expected, fields['x'], fields['m'], size))
        return tuple(result)

    def _image_row(self, row, ordinal, image, event, grant):
        if row is None:
            return
        if ((row.target_id, row.source_id, row.ordinal, row.revision, row.scope_hash,
                row.source_at, row.content_hash, row.mime_type, row.byte_size) !=
                (grant.target_id, event['id'], ordinal, grant.revision, grant.scope_hash,
                 event['created_at'], image.digest, image.mime, image.size)
                or row.state not in ('unknown', 'acked')
                or row.image_key and not BotLarkCli._image_key(row.image_key)
                or row.state == 'acked' and not row.image_key):
            raise _Pending('authorization')

    async def _image_guard(self, event, grant, *, upload=False):
        if 'message' not in grant.evidence.capabilities:
            raise _Pending('unsupported')
        await self._source(event, grant)
        self._reaction_current(grant)
        if await self._edit_history(event['id'], grant):
            raise _Pending('unsupported')
        self._reaction_current(grant)
        await self._fresh(grant)
        if upload:
            scopes = await thread_call(self.bot.bot_scopes)
            self._reaction_current(grant)
            if not {'im:resource:upload', 'im:resource'} & scopes:
                raise _Pending('unsupported')
        await thread_call(self._files_identity)
        self._reaction_current(grant)

    def _images_confirmed(self, event, grant, images, keys):
        self._reaction_current(grant)
        if len(images) != len(keys) or not 1 <= len(images) <= 4:
            raise _Pending('receipt')
        if any(self.store.remote_image_upload_by_source(grant.target_id, event['id'], ordinal) is not None
               for ordinal in range(len(images), 4)):
            raise _Pending('authorization')
        for ordinal, (image, key) in enumerate(zip(images, keys)):
            row = self.store.remote_image_upload_by_source(grant.target_id, event['id'], ordinal)
            self._image_row(row, ordinal, image, event, grant)
            if row is None or row.state != 'acked' or row.image_key != key:
                raise _Pending('uncertain')

    def _original_image_rows(self, original, grant):
        """Return the immutable ACKed original image tuple; never repairs it."""
        images = self._image_metadata(original)
        if not images:
            if any(self.store.remote_image_upload_by_source(grant.target_id, original['id'], ordinal) is not None
                   for ordinal in range(4)):
                raise _Pending('authorization')
            return (), ()
        if not 1 <= len(images) <= 4:
            raise _Pending('unsupported')
        rows = tuple(self.store.remote_image_upload_by_source(
            grant.target_id, original['id'], ordinal) for ordinal in range(len(images)))
        for ordinal, (image, row) in enumerate(zip(images, rows)):
            self._image_row(row, ordinal, image, original, grant)
            if row is None or row.state != 'acked' or not row.image_key:
                raise _Pending('uncertain')
        if any(self.store.remote_image_upload_by_source(grant.target_id, original['id'], ordinal) is not None
               for ordinal in range(len(images), 4)):
            raise _Pending('authorization')
        delivery = self.store.remote_delivery_by_source(grant.target_id, original['id'], 'message')
        keys = tuple(row.image_key for row in rows)
        card = self._image_card(original, grant, keys)
        expected = (grant.target_id, original['id'], 'message', grant.revision, grant.scope_hash, original['created_at'],
                    gs.buzz_thread_root(original) or '', hashlib.sha256(card.encode()).hexdigest(),
                    grant.evidence.app_id)
        if delivery is None or delivery.status != 'acked' or (
                delivery.target_id, delivery.source_id, delivery.action, delivery.revision,
                delivery.scope_hash, delivery.source_at, delivery.root_id,
                delivery.content_hash, delivery.sender_app_id) != expected or not delivery.message_id:
            raise _Pending('uncertain')
        return images, rows

    async def _original_image_action_snapshot(self, original, grant, action_event, action_kind):
        """Recheck signed inputs, complete overlays, proof and exact SQL image tuple."""
        current_original = await self._source(original, grant)
        self._reaction_current(grant)
        if current_original != original:
            raise _Pending('source')
        include = ()
        if action_kind == 'edit':
            current_action = await self._edit_source(action_event, grant)
            self._reaction_current(grant)
            if current_action != action_event or gs.edit_target(current_action) != original['id']:
                raise _Pending('source')
            include = (current_action,)
        elif action_kind == 'reaction':
            current_action = await self._reaction_source(action_event, grant)
            self._reaction_current(grant)
            if current_action != action_event:
                raise _Pending('source')
            action_refs = [tag[1] for tag in current_action['tags'] if tag[0] == 'e']
            if len(action_refs) != 1:
                raise _Pending('source')
            if current_action['kind'] == 5:
                ref = action_refs[0]
                rows = await self.reader.query([{'kinds': [7], 'authors': [self.record.pubkey],
                    'ids': [ref], '#h': [grant.evidence.channel_id], 'limit': 2}])
                self._reaction_current(grant)
                if len(rows) != 1 or rows[0]['id'] != ref:
                    raise _Pending('source')
                added = await self._reaction_source(rows[0], grant)
                self._reaction_current(grant)
                if next(tag[1] for tag in added['tags'] if tag[0] == 'e') != original['id']:
                    raise _Pending('source')
            elif action_refs[0] != original['id']:
                raise _Pending('source')
        elif action_kind != 'message':
            raise _Pending('authorization')
        history = await self._edit_history(original['id'], grant, include)
        self._reaction_current(grant)
        await self._fresh(grant)
        self._reaction_current(grant)
        self._original_image_rows(original, grant)
        return history

    async def _original_image_keys(self, original, grant, *, action_event=None, action_kind='message'):
        """GET every pinned original key; no reserve, upload, key adoption or ACK."""
        images, rows = self._original_image_rows(original, grant)
        if not images:
            include = (action_event,) if action_kind == 'edit' else ()
            return (), await self._edit_history(original['id'], grant, include)
        history = await self._original_image_action_snapshot(original, grant, action_event, action_kind)
        for image, row in zip(images, rows):
            self._reaction_current(grant)
            current_images, current_rows = self._original_image_rows(original, grant)
            if current_images != images or current_rows != rows:
                raise _Pending('authorization')
            receipt = await thread_call(self.bot.own_image, row.image_key)
            self._reaction_current(grant)
            if (type(receipt) is not OwnImage
                    or (receipt.image_key, receipt.content_hash, receipt.mime_type, receipt.byte_size)
                        != (row.image_key, image.digest, image.mime, image.size)):
                raise _Pending('receipt')
            history = await self._original_image_action_snapshot(original, grant, action_event, action_kind)
            current_images, current_rows = self._original_image_rows(original, grant)
            if current_images != images or current_rows != rows:
                raise _Pending('authorization')
        return tuple(row.image_key for row in rows), history

    async def _prepare_images(self, event, grant, images):
        # Image edits are intentionally not supported in this slice. A current
        # signed overlay cannot silently strip pictures during message recovery.
        if await self._edit_history(event['id'], grant):
            raise _Pending('unsupported')
        existing_message = self.store.remote_delivery_by_source(grant.target_id, event['id'], 'message')
        rows = [self.store.remote_image_upload_by_source(grant.target_id, event['id'], i) for i in range(4)]
        for ordinal, row in enumerate(rows):
            if ordinal >= len(images):
                if row is not None:
                    raise _Pending('authorization')
                continue
            self._image_row(row, ordinal, images[ordinal], event, grant)
            if row is not None and not row.image_key:
                # Original keyless UNKNOWN is never a lease to upload again.
                raise _Pending('uncertain')
            if existing_message is not None and row is None:
                raise _Pending('uncertain')
        needs_upload = any(row is None for row in rows[:len(images)])
        bodies = []
        if needs_upload:
            # Validate ALL signed metadata and media before the first image
            # reservation or native upload. Never send a partial image set.
            for image in images:
                raw = await self.reader.read_media(image.url, sha256=image.digest, mime=image.mime, size=image.size)
                self._reaction_current(grant)
                if (type(raw) is not bytes or len(raw) != image.size
                        or hashlib.sha256(raw).hexdigest() != image.digest):
                    raise _Pending('source')
                BotLarkCli._image_body(raw, image.mime)
                bodies.append(raw)
                await thread_call(self._files)
                self._reaction_current(grant)
            await self._image_guard(event, grant, upload=True)
        keys = []
        for ordinal, image in enumerate(images):
            row = self.store.remote_image_upload_by_source(grant.target_id, event['id'], ordinal)
            self._image_row(row, ordinal, image, event, grant)
            created = False
            if row is None:
                await self._image_guard(event, grant, upload=True)
                with self.store.transaction():
                    self._reaction_current(grant)
                    prior = self.store.remote_image_upload_by_source(grant.target_id, event['id'], ordinal)
                    row = self.store.reserve_remote_image_upload(grant.target_id, event['id'], ordinal,
                        grant.revision, grant.scope_hash, event['created_at'], image.digest,
                        image.mime, image.size, now=self.clock())
                    if row is None:
                        raise _Pending('authorization')
                    self._image_row(row, ordinal, image, event, grant)
                    created = prior is None
                # UNKNOWN is committed before physical IO. Only this original
                # newly reserved live attempt may upload; recovery cannot adopt it.
                if created:
                    key = await thread_call(self.bot.upload_image, bodies[ordinal], image.mime)
                    self._reaction_current(grant)
                    # Pin the actual returned key synchronously before ANY await.
                    if not self.store.pin_remote_image_key(grant.target_id, event['id'], ordinal,
                            grant.revision, grant.scope_hash, key, now=self.clock()):
                        raise _Pending('authorization')
                    row = self.store.remote_image_upload_by_source(grant.target_id, event['id'], ordinal)
                    self._image_row(row, ordinal, image, event, grant)
            if row is None or not row.image_key:
                raise _Pending('uncertain')
            await self._image_guard(event, grant)
            receipt = await thread_call(self.bot.own_image, row.image_key)
            self._reaction_current(grant)
            if (type(receipt) is not OwnImage
                    or (receipt.image_key, receipt.content_hash, receipt.mime_type, receipt.byte_size) !=
                        (row.image_key, image.digest, image.mime, image.size)):
                raise _Pending('receipt')
            await self._image_guard(event, grant)
            current = self.store.remote_image_upload_by_source(grant.target_id, event['id'], ordinal)
            self._image_row(current, ordinal, image, event, grant)
            if current is None or current.image_key != row.image_key:
                raise _Pending('authorization')
            if current.state != 'acked' and not self.store.ack_remote_image_upload(
                    grant.target_id, event['id'], ordinal, grant.revision, grant.scope_hash,
                    row.image_key, image.digest, image.mime, image.size, now=self.clock()):
                raise _Pending('authorization')
            keys.append(row.image_key)
        self._images_confirmed(event, grant, images, keys)
        return tuple(keys)

    def _image_card(self, event, grant, keys):
        return self._card_with_images(event, event['content'], grant.evidence.channel_id, keys)

    def _card_with_images(self, event, content, channel, keys):
        card = json.loads(message_card(event, self.record.name, content,
                                      self.link_base, channel))
        elements = card.get('elements')
        if not isinstance(elements, list) or not elements:
            raise _Pending('receipt')
        elements[-1:-1] = [{'tag': 'img', 'img_key': key,
                            'alt': {'tag': 'plain_text', 'content': ''}} for key in keys]
        return json.dumps(card, ensure_ascii=False, separators=(',', ':'))

    @staticmethod
    def _valid_edit(event, channel, author):
        try:
            tags = event.get('tags') if isinstance(event, dict) else None
            if (not isinstance(tags, list) or len(tags) > 4096
                    or not all(isinstance(tag, list) and 0 < len(tag) <= 64
                               and all(isinstance(value, str) and len(value) <= 8192 for value in tag)
                               for tag in tags)):
                return False
            channels = [tag for tag in tags if tag[0] == 'h']
            refs = [tag for tag in tags if tag[0] == 'e']
            return (type(event.get('kind')) is int and event['kind'] == 40003
                    and event.get('pubkey') == author and type(event.get('created_at')) is int
                    and 0 <= event['created_at'] < 2 ** 63
                    and isinstance(event.get('content'), str)
                    and len(event['content'].encode()) <= 2 ** 20
                    and len(channels) == 1 and channels[0] == ['h', channel]
                    and len(refs) == 1 and len(refs[0]) == 2
                    and bool(gs.HEX64_RE.fullmatch(refs[0][1]))
                    and not any(tag[0] == 'imeta' for tag in tags)
                    and gs._nip01_event_verified(event))
        except Exception:
            return False

    async def _edit_source(self, event, grant):
        channel = grant.evidence.channel_id
        valid = await thread_call(self._valid_edit, event, channel, self.record.pubkey)
        self._reaction_current(grant)
        if not valid:
            raise _Pending('source')
        rows = await self.reader.query([{'kinds': [40003], 'authors': [self.record.pubkey],
            'ids': [event['id']], '#h': [channel], 'limit': 2}])
        self._reaction_current(grant)
        if len(rows) != 1 or rows[0] != event:
            raise _Pending('source')
        return rows[0]

    @staticmethod
    def _edit_history_snapshot(original_id, channel, author, rows, include):
        if (not isinstance(rows, list) or len(rows) >= MAX_EDITS
                or type(include) is not tuple or len(include) > 1):
            raise _Pending('incomplete')
        seen = {}
        for event in rows:
            if not RemoteOutlet._valid_edit(event, channel, author):
                raise _Pending('source')
            if gs.edit_target(event) != original_id:
                raise _Pending('source')
            if event['id'] in seen:
                raise _Pending('incomplete')
            seen[event['id']] = event
        for event in include:
            if not RemoteOutlet._valid_edit(event, channel, author):
                raise _Pending('source')
            if gs.edit_target(event) != original_id:
                raise _Pending('source')
            prior = seen.get(event['id'])
            if prior is not None and prior != event:
                raise _Pending('incomplete')
            seen[event['id']] = event
        if len(seen) >= MAX_EDITS:
            raise _Pending('incomplete')
        return tuple(seen.values())

    async def _edit_history(self, original_id, grant, include=()):
        rows = await self.reader.query([{'kinds': [40003], 'authors': [self.record.pubkey],
            '#h': [grant.evidence.channel_id], '#e': [original_id], 'limit': 257}])
        self._reaction_current(grant)
        snapshot = await thread_call(self._edit_history_snapshot, original_id,
            grant.evidence.channel_id, self.record.pubkey, rows, include)
        self._reaction_current(grant)
        return snapshot

    def _edit_card(self, original, edit, channel, image_keys=()):
        if image_keys:
            card = self._card_with_images(original, edit['content'], channel, image_keys)
        else:
            card = message_card(original, self.record.name, edit['content'], self.link_base, channel)
        if len(card.encode('utf-8')) >= MAX_PATCH_CONTENT_BYTES:
            raise _Pending('unsupported')
        return card

    async def _latest_pinned_card(self, original, grant, *, allow_unreserved_latest=False,
                                  image_keys=None, history=None):
        original_id = original['id']
        root_id = gs.buzz_thread_root(original) or ''
        if history is None:
            history = await self._edit_history(original_id, grant)
        if image_keys is None:
            images = self._image_metadata(original)
            if images:
                image_keys, history = await self._original_image_keys(original, grant)
        image_keys = () if image_keys is None else image_keys
        pinned = []
        for event in history:
            row = self.store.remote_delivery_by_source(grant.target_id, event['id'], 'edit')
            if row is None or row.status not in ('unknown', 'acked'):
                continue
            card = self._edit_card(original, event, grant.evidence.channel_id, image_keys)
            if ((row.revision, row.scope_hash, row.source_at, row.root_id, row.content_hash)
                    != (grant.revision, grant.scope_hash, event['created_at'], root_id,
                        hashlib.sha256(card.encode('utf-8')).hexdigest())):
                raise _Pending('authorization')
            if row.status == 'acked' and (row.sender_app_id != grant.evidence.app_id
                                           or not row.message_id):
                raise _Pending('receipt')
            pinned.append((event, card, row))
        if not pinned:
            if any(row is not None and row.status == 'reserved'
                   for event in history for row in [self.store.remote_delivery_by_source(
                       grant.target_id, event['id'], 'edit')]):
                raise _Pending('uncertain')
            if image_keys:
                return self._image_card(original, grant, image_keys), None
            return message_card(original, self.record.name, original['content'],
                                self.link_base, grant.evidence.channel_id), None
        event, card, row = max(pinned, key=lambda item: (item[0]['created_at'], item[0]['id']))
        latest_signed = max(history, key=lambda item: (item['created_at'], item['id'])) if history else event
        if (not allow_unreserved_latest
                and (latest_signed['created_at'], latest_signed['id']) > (event['created_at'], event['id'])):
            raise _Pending('uncertain')
        return card, (event, row)

    @staticmethod
    def _patch_window_open(row, now):
        if type(now) is not int or now < 0:
            return False
        value = row.get('create_time') if isinstance(row, dict) else None
        if type(value) is int:
            millis = value
        elif isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 16:
            millis = int(value)
        else:
            return False
        current = now * 1000
        return 0 < millis <= current and current - millis < PATCH_WINDOW_SECONDS * 1000

    async def _edit_context(self, original, grant, action_event):
        resolved = await self.mappings.resolve_buzz(original['id'])
        self._reaction_current(grant)
        if (resolved.status != 'verified' or resolved.mapping is None
                or resolved.mapping.event_id != original['id']):
            raise _Pending('root')
        mapping = resolved.mapping
        if (mapping.sender_app_id not in ('', grant.evidence.app_id)
                or (mapping.buzz_root or '') != (gs.buzz_thread_root(original) or '')):
            raise _Pending('root')
        image_keys, history = await self._original_image_keys(original, grant,
            action_event=action_event, action_kind='edit')
        card, pinned = await self._latest_pinned_card(original, grant,
            allow_unreserved_latest=True, image_keys=image_keys, history=history)
        current_resolved = await self.mappings.resolve_buzz(original['id'])
        self._reaction_current(grant)
        if (current_resolved.status != 'verified' or current_resolved.mapping is None
                or (current_resolved.mapping.event_id, current_resolved.mapping.message_id,
                    current_resolved.mapping.feishu_root, current_resolved.mapping.buzz_root)
                   != (mapping.event_id, mapping.message_id, mapping.feishu_root, mapping.buzz_root)):
            raise _Pending('root')
        mapping = current_resolved.mapping
        row = await thread_call(self.bot.message_view, mapping.message_id, 'open_id')
        self._reaction_current(grant)
        sender = {'sender_type': 'app', 'id_type': 'app_id', 'id': grant.evidence.app_id}
        if (not isinstance(row, dict) or row.get('message_id') != mapping.message_id
                or row.get('chat_id') != grant.evidence.chat_id or row.get('deleted')
                or row.get('msg_type') != 'interactive'
                or any((row.get('sender') or {}).get(key) != value for key, value in sender.items())
                or (row.get('root_id') or mapping.message_id) != mapping.feishu_root):
            raise _Pending('root')
        raw = (row.get('body') or {}).get('content')
        try:
            exact = isinstance(raw, str) and json.loads(raw) == json.loads(card)
        except Exception:
            exact = False
        if not exact or not isinstance(raw, str) or len(raw.encode('utf-8')) >= MAX_PATCH_CONTENT_BYTES:
            raise _Pending('receipt')
        if pinned is not None and pinned[1].status == 'acked' and pinned[1].message_id != mapping.message_id:
            raise _Pending('receipt')
        if image_keys:
            current_history = await self._original_image_action_snapshot(
                original, grant, action_event, 'edit')
            if sorted(current_history, key=lambda row: row['id']) != sorted(history, key=lambda row: row['id']):
                raise _Pending('source')
        self._original_image_rows(original, grant)
        return mapping, row, image_keys, history

    async def _edit_original(self, event, grant):
        original_id = gs.edit_target(event)
        if not original_id:
            raise _Pending('source')
        rows = await self.reader.query([{'kinds': [9], 'authors': [self.record.pubkey],
            'ids': [original_id], '#h': [grant.evidence.channel_id], 'limit': 2}])
        self._reaction_current(grant)
        if (not isinstance(rows, list) or len(rows) != 1 or rows[0].get('id') != original_id
                or rows[0].get('pubkey') != self.record.pubkey
                or not await thread_call(_verified, rows[0], grant.evidence.channel_id)):
            raise _Pending('source')
        self._reaction_current(grant)
        self._image_metadata(rows[0])
        return rows[0]

    async def _deliver_edit(self, event, grant):
        if 'edit' not in grant.evidence.capabilities:
            raise _Pending('unsupported')
        event = await self._edit_source(event, grant)
        original = await self._edit_original(event, grant)
        mapping, native, image_keys, history = await self._edit_context(original, grant, event)
        latest = max(history, key=lambda row: (row['created_at'], row['id']))
        existing = self.store.remote_delivery_by_source(grant.target_id, event['id'], 'edit')
        if existing is not None:
            if (existing.revision, existing.scope_hash, existing.source_at, existing.root_id) != (
                    grant.revision, grant.scope_hash, event['created_at'],
                    gs.buzz_thread_root(original) or ''):
                raise _Pending('authorization')
            if existing.status == 'reserved':
                raise _Pending('uncertain')
            if existing.status == 'unknown' and latest['id'] != event['id']:
                raise _Pending('uncertain')
        elif latest['id'] != event['id']:
            return OutletOutcome('ignored', 'superseded', source_id=event['id'])

        card = self._edit_card(original, event, grant.evidence.channel_id, image_keys)
        content_hash = hashlib.sha256(card.encode('utf-8')).hexdigest()
        if existing is not None and existing.content_hash != content_hash:
            raise _Pending('authorization')
        if existing is not None and existing.status == 'acked':
            if (existing.sender_app_id != grant.evidence.app_id
                    or existing.message_id != mapping.message_id):
                raise _Pending('receipt')
        elif existing is None:
            if not self._patch_window_open(native, self.clock()):
                raise _Pending('unsupported')

        await self._fresh(grant)
        self._reaction_current(grant)
        self._original_image_rows(original, grant)
        if existing is None:
            if not self._patch_window_open(native, self.clock()):
                raise _Pending('unsupported')
            with self.store.transaction():
                reservation = self.store.reserve_remote_delivery(grant.target_id, event['id'], 'edit',
                    revision=grant.revision, scope_hash=grant.scope_hash, source_at=event['created_at'],
                    content_hash=content_hash, root_id=gs.buzz_thread_root(original) or '', now=self.clock())
                delivery = reservation.record
                if (delivery.revision, delivery.scope_hash) != (grant.revision, grant.scope_hash):
                    raise _Pending('authorization')
                if reservation.created and not self.store.mark_remote_unknown(delivery.id,
                        revision=delivery.revision, scope_hash=delivery.scope_hash, now=self.clock()):
                    raise _Pending('authorization')
            existing = delivery
            if reservation.created:
                await thread_call(self.bot.update_card, mapping.message_id, card)
                self._reaction_current(grant)
        else:
            delivery = existing

        # Re-read the signed input, original, complete overlay set, mapping, and
        # exact native body after PATCH (or on GET-only UNKNOWN recovery).
        current_event = await self._edit_source(event, grant)
        current_original = await self._edit_original(current_event, grant)
        current_mapping, current_native, current_keys, current_history = await self._edit_context(
            current_original, grant, current_event)
        current_latest = max(current_history, key=lambda row: (row['created_at'], row['id']))
        current_card = self._edit_card(current_original, current_event,
                                       grant.evidence.channel_id, current_keys)
        if (current_event != event or current_latest['id'] != event['id']
                or current_original != original
                or (current_mapping.message_id, current_mapping.feishu_root, current_mapping.buzz_root)
                    != (mapping.message_id, mapping.feishu_root, mapping.buzz_root)
                or current_native.get('message_id') != mapping.message_id
                or json.loads(current_native['body']['content']) != json.loads(current_card)
                or current_card != card):
            raise _Pending('receipt')
        await self._fresh(grant)
        self._reaction_current(grant)
        self._original_image_rows(original, grant)
        receipt_hash = Store._remote_digest([delivery.id, content_hash, grant.evidence.app_id,
                                            mapping.message_id, mapping.feishu_root, event['id']])
        with self.store.transaction():
            self._reaction_current(grant)
            if not self.store.ack_remote_delivery(delivery.id, revision=delivery.revision,
                    scope_hash=delivery.scope_hash, sender_app_id=grant.evidence.app_id,
                    message_id=mapping.message_id, receipt_hash=receipt_hash, now=self.clock()):
                raise _Pending('authorization')
        return OutletOutcome('acked', source_id=event['id'], message_id=mapping.message_id)

    async def _root(self, event):
        root = gs.buzz_thread_root(event)
        if not root: return ''
        result = await self.mappings.resolve_buzz(root)
        if result.status != 'verified' or result.mapping.event_id != root:
            raise _Pending('root')
        # A reply targets the proven canonical root, not an arbitrary reply ID.
        return result.mapping.feishu_root

    async def _receipt(self, mid, event, card, parent, grant):
        if not isinstance(mid, str) or not gs.MESSAGE_ID_RE.fullmatch(mid):
            raise _Pending('receipt')
        row = await thread_call(self.bot.message_view, mid, 'open_id')
        expected_sender = {'sender_type': 'app', 'id_type': 'app_id', 'id': grant.evidence.app_id}
        if (not isinstance(row, dict) or row.get('message_id') != mid
                or row.get('chat_id') != grant.evidence.chat_id or row.get('deleted')
                or row.get('msg_type') != 'interactive'
                or any((row.get('sender') or {}).get(k) != v for k, v in expected_sender.items())
                or (row.get('root_id') or mid) != (parent or mid)):
            raise _Pending('receipt')
        raw = (row.get('body') or {}).get('content')
        # Canonical JSON equality permits serialization whitespace, never a
        # footer-only adoption of a message with a different card/body.
        if not isinstance(raw, str) or json.loads(raw) != json.loads(card):
            raise _Pending('receipt')
        mapping = await self.mappings.resolve_feishu(mid)
        if (mapping.status != 'verified' or mapping.mapping.event_id != event['id']
                or mapping.mapping.message_id != mid
                or mapping.mapping.feishu_root != (parent or mid)
                or (mapping.mapping.buzz_root or '') != (gs.buzz_thread_root(event) or '')):
            raise _Pending('receipt')
        return mid

    async def _recover(self, event, card, parent, grant):
        if parent:
            rows, more = await thread_call(self.bot.thread_messages, parent)
        else:
            since = datetime.fromtimestamp(max(0, event['created_at'] - 900), timezone.utc)
            rows, more = await thread_call(self.bot.messages, grant.evidence.chat_id, since,
                                            order='desc', page_limit=2)
        if more or len(rows) > MAX_HISTORY:
            raise _Pending('incomplete')
        seen, matches = set(), []
        for row in rows:
            mid = row.get('message_id')
            if not isinstance(mid, str) or not gs.MESSAGE_ID_RE.fullmatch(mid) or mid in seen:
                raise _Pending('incomplete')
            seen.add(mid)
            try:
                matches.append(await self._receipt(mid, event, card, parent, grant))
            except _Pending:
                continue
        if len(matches) != 1:
            raise _Pending('uncertain')
        return matches[0]

    def _reaction_current(self, grant):
        """Pure SQL/identity gate after each awaited reaction read or write."""
        self._identity(grant)
        with self.store.transaction():
            current = self.store.remote_grant(grant.target_id)
            target = self.store.conn.execute('SELECT status,current_revision FROM remote_target WHERE target_id=?',
                                             (grant.target_id,)).fetchone()
            agent = self.store.conn.execute('SELECT status,owner_pubkey,app_id,config_path FROM agent WHERE pubkey=?',
                                            (self.record.pubkey,)).fetchone()
            local = self.store.conn.execute('SELECT 1 FROM binding WHERE channel_id=? LIMIT 1',
                                            (grant.evidence.channel_id,)).fetchone()
            if (local is not None or target is None or target['status'] != 'active'
                    or target['current_revision'] != grant.revision or current is None
                    or (current.revision, current.scope_hash) != (grant.revision, grant.scope_hash)
                    or Store._remote_scope(current.evidence) != grant.scope_hash
                    or not self.store._remote_approval(current.evidence)
                    or agent is None or agent['status'] != 'active'
                    or (agent['owner_pubkey'], agent['app_id'], agent['config_path'])
                        != (self.record.owner_pubkey, self.record.app_id, str(self.record.env_file))):
                raise _Pending('authorization')

    @staticmethod
    def _reaction_valid(event, channel):
        if (not isinstance(event, dict) or type(event.get('kind')) is not int
                or event['kind'] not in (7, 5) or not gs._nip01_event_verified(event)):
            return False
        tags = event.get('tags')
        if (not isinstance(tags, list)
                or not all(isinstance(tag, list) and tag and all(isinstance(v, str) for v in tag) for tag in tags)):
            return False
        refs = [tag for tag in tags if tag[0] == 'e']
        return ([tag for tag in tags if tag[0] == 'h'] == [['h', channel]]
                and len(refs) == 1 and len(refs[0]) == 2 and bool(gs.HEX64_RE.fullmatch(refs[0][1]))
                and (event['kind'] != 5 or event.get('content') == ''))

    async def _reaction_source(self, event, grant):
        if (not isinstance(event, dict) or event.get('pubkey') != self.record.pubkey
                or not await thread_call(self._reaction_valid, event, grant.evidence.channel_id)):
            raise _Pending('source')
        self._reaction_current(grant)
        rows = await self.reader.query([{'kinds': [event['kind']], 'authors': [self.record.pubkey],
            'ids': [event['id']], '#h': [grant.evidence.channel_id], 'limit': 2}])
        self._reaction_current(grant)
        if len(rows) != 1 or rows[0] != event:
            raise _Pending('source')
        return rows[0]

    async def _reaction_original(self, source_id, grant, action_event):
        rows = await self.reader.query([{'kinds': [9], 'authors': [self.record.pubkey],
            'ids': [source_id], '#h': [grant.evidence.channel_id], 'limit': 2}])
        self._reaction_current(grant)
        if (len(rows) != 1 or rows[0]['id'] != source_id or rows[0]['pubkey'] != self.record.pubkey
                or not await thread_call(_verified, rows[0], grant.evidence.channel_id)):
            raise _Pending('source')
        self._reaction_current(grant)
        original = rows[0]
        self._image_metadata(original)
        resolved = await self.mappings.resolve_buzz(source_id)
        self._reaction_current(grant)
        if resolved.status != 'verified' or resolved.mapping.event_id != source_id:
            raise _Pending('root')
        mapping = resolved.mapping
        image_keys, history = await self._original_image_keys(original, grant,
            action_event=action_event, action_kind='reaction')
        current_resolved = await self.mappings.resolve_buzz(source_id)
        self._reaction_current(grant)
        if (current_resolved.status != 'verified' or current_resolved.mapping is None
                or (current_resolved.mapping.event_id, current_resolved.mapping.message_id,
                    current_resolved.mapping.feishu_root, current_resolved.mapping.buzz_root)
                   != (mapping.event_id, mapping.message_id, mapping.feishu_root, mapping.buzz_root)):
            raise _Pending('root')
        mapping = current_resolved.mapping
        row = await thread_call(self.bot.message_view, mapping.message_id, 'open_id')
        self._reaction_current(grant)
        sender = {'sender_type': 'app', 'id_type': 'app_id', 'id': grant.evidence.app_id}
        card, pinned = await self._latest_pinned_card(original, grant,
            image_keys=image_keys, history=history)
        self._reaction_current(grant)
        if image_keys and pinned is not None and pinned[1].status != 'acked':
            raise _Pending('uncertain')
        if (not isinstance(row, dict) or row.get('message_id') != mapping.message_id
                or row.get('chat_id') != grant.evidence.chat_id or row.get('deleted')
                or row.get('msg_type') != 'interactive'
                or any((row.get('sender') or {}).get(k) != v for k, v in sender.items())
                or (row.get('root_id') or mapping.message_id) != mapping.feishu_root
                or (mapping.buzz_root or '') != (gs.buzz_thread_root(original) or '')):
            raise _Pending('root')
        raw = (row.get('body') or {}).get('content')
        if not isinstance(raw, str) or json.loads(raw) != json.loads(card):
            raise _Pending('receipt')
        if image_keys:
            current_history = await self._original_image_action_snapshot(
                original, grant, action_event, 'reaction')
            if sorted(current_history, key=lambda row: row['id']) != sorted(history, key=lambda row: row['id']):
                raise _Pending('source')
        self._original_image_rows(original, grant)
        return original, mapping

    async def _reaction_snapshot(self, mapping, emoji, grant, reaction_id=''):
        snapshot = await thread_call(self.bot.own_reactions, mapping.message_id, emoji,
                                     expected_reaction_id=reaction_id)
        self._reaction_current(grant)
        if (snapshot.complete is not True or snapshot.message_id != mapping.message_id
                or snapshot.app_id != grant.evidence.app_id or snapshot.emoji != emoji):
            raise _Pending('incomplete')
        return snapshot.reaction_ids

    def _created_reaction_id(self, data, emoji):
        operator = data.get('operator') if isinstance(data, dict) else None
        kind = data.get('reaction_type') if isinstance(data, dict) else None
        rid = data.get('reaction_id') if isinstance(data, dict) else None
        if (not isinstance(operator, dict) or operator.get('operator_type') != 'app'
                or operator.get('operator_id') != self.record.app_id
                or not isinstance(kind, dict) or kind.get('emoji_type') != emoji
                or not isinstance(rid, str) or not re.fullmatch(
                    r'(?:[A-Za-z0-9_.:-]{1,256}|[A-Za-z0-9_-]{85}[AQgw]==)', rid)):
            raise _Pending('receipt')
        return rid

    async def _deliver_reaction(self, event, grant):
        event = await self._reaction_source(event, grant)
        action = 'reaction_add' if event['kind'] == 7 else 'reaction_remove'
        if action not in grant.evidence.capabilities:
            raise _Pending('unsupported')
        ref = next(tag[1] for tag in event['tags'] if tag[0] == 'e')
        added, receipt, rid = event, None, ''
        if action == 'reaction_remove':
            rows = await self.reader.query([{'kinds': [7], 'authors': [self.record.pubkey],
                'ids': [ref], '#h': [grant.evidence.channel_id], 'limit': 2}])
            self._reaction_current(grant)
            if len(rows) != 1 or rows[0]['id'] != ref:
                raise _Pending('source')
            added = await self._reaction_source(rows[0], grant)
            receipt = self.store.remote_delivery_by_source(grant.target_id, ref, 'reaction_add')
            if (receipt is None or receipt.status != 'acked'
                    or (receipt.revision, receipt.scope_hash) != (grant.revision, grant.scope_hash)
                    or receipt.sender_app_id != grant.evidence.app_id):
                raise _Pending('receipt')
            rid = receipt.reaction_id
        emoji = gs.DEFAULT_REACTION_MAP.get(gs._reaction_emoji(added['content']))
        if not emoji:
            raise _Pending('unsupported')
        root_id = next(tag[1] for tag in added['tags'] if tag[0] == 'e')
        _, mapping = await self._reaction_original(root_id, grant, event)
        if receipt is not None and (receipt.message_id, receipt.root_id, receipt.emoji) != (mapping.message_id, root_id, emoji):
            raise _Pending('receipt')
        content_hash = Store._remote_digest([action, root_id, mapping.message_id, mapping.feishu_root, emoji,
                                            ref if action == 'reaction_remove' else '', rid])
        existing = self.store.remote_delivery_by_source(grant.target_id, event['id'], action)
        if existing is not None:
            if (existing.revision, existing.scope_hash, existing.root_id, existing.content_hash) != (
                    grant.revision, grant.scope_hash, root_id, content_hash):
                raise _Pending('authorization')
            if existing.status == 'acked':
                if (existing.sender_app_id, existing.message_id, existing.emoji) != (grant.evidence.app_id, mapping.message_id, emoji):
                    raise _Pending('receipt')
                rid = existing.reaction_id
        own = await self._reaction_snapshot(mapping, emoji, grant, rid)
        if action == 'reaction_add':
            if (existing is None and own) or (existing is not None and existing.status == 'acked' and own != (rid,)):
                raise _Pending('receipt')
        elif own != ((rid,) if existing is None else ()):
            # Existing UNKNOWN removal reconciles only exact original absence;
            # another own ID or foreign original ID never proves that absence.
            raise _Pending('uncertain')
        _, current_mapping = await self._reaction_original(root_id, grant, event)
        if (current_mapping.message_id, current_mapping.feishu_root, current_mapping.buzz_root) != (
                mapping.message_id, mapping.feishu_root, mapping.buzz_root):
            raise _Pending('root')
        await self._fresh(grant)
        self._reaction_current(grant)
        with self.store.transaction():
            reservation = self.store.reserve_remote_delivery(grant.target_id, event['id'], action,
                revision=grant.revision, scope_hash=grant.scope_hash, source_at=event['created_at'],
                content_hash=content_hash, root_id=root_id, now=self.clock())
            delivery = reservation.record
            if (delivery.revision, delivery.scope_hash) != (grant.revision, grant.scope_hash):
                raise _Pending('authorization')
            if reservation.created and not self.store.mark_remote_unknown(delivery.id,
                    revision=delivery.revision, scope_hash=delivery.scope_hash, now=self.clock()):
                raise _Pending('authorization')
        if reservation.created:
            params = {'message_id': mapping.message_id}
            args = ['im', 'reactions', 'create' if action == 'reaction_add' else 'delete', '--params']
            if action == 'reaction_remove':params['reaction_id'] = rid
            args.append(json.dumps(params, separators=(',', ':')))
            if action == 'reaction_add':args += ['--data', json.dumps({'reaction_type': {'emoji_type': emoji}}, separators=(',', ':'))]
            args += ['--as', 'bot']
            data = await thread_call(self.bot.call, 'remote reaction mutation', args)
            self._reaction_current(grant)
            if action == 'reaction_add':rid = self._created_reaction_id(data, emoji)
            own = await self._reaction_snapshot(mapping, emoji, grant, rid)
            if own != ((rid,) if action == 'reaction_add' else ()):
                raise _Pending('receipt')
        elif action == 'reaction_add' and delivery.status != 'acked':
            # UNKNOWN cannot store a durable native ID in this schema. A later
            # singleton own reaction is not correlated to this signed source.
            raise _Pending('uncertain')
        # Native target/body/root can change during the physical operation or
        # readback. Recheck the same original mapping before current proof/CAS;
        # an unchanged reaction ID alone cannot authorize a stale target ACK.
        _, current_mapping = await self._reaction_original(root_id, grant, event)
        if (current_mapping.message_id, current_mapping.feishu_root, current_mapping.buzz_root) != (
                mapping.message_id, mapping.feishu_root, mapping.buzz_root):
            raise _Pending('root')
        await self._fresh(grant)
        self._reaction_current(grant)
        receipt_hash = Store._remote_digest([delivery.id, content_hash, grant.evidence.app_id,
                                            mapping.message_id, mapping.feishu_root, emoji, rid])
        with self.store.transaction():
            self._reaction_current(grant)
            if not self.store.ack_remote_delivery(delivery.id, revision=delivery.revision,
                    scope_hash=delivery.scope_hash, sender_app_id=grant.evidence.app_id,
                    message_id=mapping.message_id, reaction_id=rid, emoji=emoji,
                    receipt_hash=receipt_hash, now=self.clock()):
                raise _Pending('authorization')
        return OutletOutcome('acked', source_id=event['id'], message_id=mapping.message_id)

    async def _deliver(self, event, target_id):
        source = event.get('id', '') if isinstance(event, dict) else ''
        if not isinstance(source, str) or not gs.HEX64_RE.fullmatch(source): source = ''
        try:
            grant = self.store.remote_grant(target_id)
            if grant is None: raise _Pending('authorization')
            # Catalog scope is checked before reads; the full fresh verifier is
            # run after source/root reads, immediately before the SQL intent.
            self._identity(grant)
            await thread_call(self._files)
            if isinstance(event, dict) and event.get('kind') in (7, 5):
                return await self._deliver_reaction(event, grant)
            if isinstance(event, dict) and event.get('kind') == 40003:
                return await self._deliver_edit(event, grant)
            event = await self._source(event, grant)
            images = self._image_metadata(event)
            parent = await self._root(event)
            existing_message = self.store.remote_delivery_by_source(target_id, event['id'], 'message')
            if images and existing_message is not None and existing_message.status == 'acked':
                history = await self._edit_history(event['id'], grant)
                if history:
                    keys, history = await self._original_image_keys(event, grant)
                    card = self._image_card(event, grant, keys)
                    receipt_card, _ = await self._latest_pinned_card(event, grant,
                        image_keys=keys, history=history)
                    mid = await self._receipt(existing_message.message_id, event, receipt_card, parent, grant)
                    current_keys, current_history = await self._original_image_keys(event, grant)
                    current_card = self._image_card(event, grant, current_keys)
                    current_receipt_card, _ = await self._latest_pinned_card(event, grant,
                        image_keys=current_keys, history=current_history)
                    if (current_keys != keys or current_card != card
                            or current_receipt_card != receipt_card):
                        raise _Pending('receipt')
                    mid = await self._receipt(mid, event, current_receipt_card, parent, grant)
                    await self._fresh(grant)
                    self._reaction_current(grant)
                    final_history = await self._original_image_action_snapshot(
                        event, grant, None, 'message')
                    if (sorted(final_history, key=lambda row: row['id'])
                            != sorted(current_history, key=lambda row: row['id'])):
                        raise _Pending('source')
                    if (self.store.remote_delivery_by_source(target_id, event['id'], 'message')
                            != existing_message):
                        raise _Pending('authorization')
                    self._original_image_rows(event, grant)
                    return OutletOutcome('acked', source_id=event['id'], message_id=mid)
            if images and existing_message is not None and existing_message.status == 'unknown':
                if await self._edit_history(event['id'], grant):
                    raise _Pending('uncertain')
            keys = await self._prepare_images(event, grant, images) if images else ()
            if images:
                card = receipt_card = self._image_card(event, grant, keys)
            else:
                card = message_card(event, self.record.name, event['content'], self.link_base, grant.evidence.channel_id)
                receipt_card, _ = await self._latest_pinned_card(event, grant)
            self._reaction_current(grant)
            content_hash = hashlib.sha256(card.encode()).hexdigest()
            # All slow reads have completed before the last fresh authority
            # check and SQL intent transaction. Only this newly created live
            # reservation may dispatch; restart recovery cannot acquire it.
            if images:
                await self._image_guard(event, grant)
                self._images_confirmed(event, grant, images, keys)
            else:
                await self._fresh(grant)
            with self.store.transaction():
                reservation = self.store.reserve_remote_delivery(target_id, event['id'], 'message',
                    revision=grant.revision, scope_hash=grant.scope_hash, source_at=event['created_at'],
                    content_hash=content_hash, root_id=gs.buzz_thread_root(event) or '', now=self.clock())
                delivery = reservation.record
                if (delivery.revision, delivery.scope_hash) != (grant.revision, grant.scope_hash):
                    raise _Pending('authorization')
                if reservation.created and not self.store.mark_remote_unknown(delivery.id,
                        revision=delivery.revision, scope_hash=delivery.scope_hash, now=self.clock()):
                    raise _Pending('authorization')
            if reservation.created:
                # Transport UUID is bounded independently of the immutable
                # 64-hex SQL identifier. Recovery never sends either again.
                uuid = 'hr-' + delivery.id[:40]
                if parent:
                    mid = await thread_call(self.bot.reply_card, parent, card, uuid)
                else:
                    mid = await thread_call(self.bot.send_card, grant.evidence.chat_id, card, uuid)
                mid = await self._receipt(mid, event, receipt_card, parent, grant)
            elif delivery.status == 'acked':
                mid = await self._receipt(delivery.message_id, event, receipt_card, parent, grant)
            else:
                # Complete absence is not proof of an unsent operation. Never
                # send again, expire unknown or adopt a newer grant revision.
                mid = await self._recover(event, receipt_card, parent, grant)
            if images:
                await self._image_guard(event, grant)
                self._images_confirmed(event, grant, images, keys)
            else:
                await self._fresh(grant)
            receipt_hash = hashlib.sha256(json.dumps([delivery.id, content_hash,
                grant.evidence.app_id, mid, parent or mid], separators=(',', ':')).encode()).hexdigest()
            with self.store.transaction():
                if delivery.status == 'reserved':
                    if not self.store.mark_remote_unknown(delivery.id, revision=delivery.revision,
                            scope_hash=delivery.scope_hash, now=self.clock()): raise _Pending('authorization')
                if not self.store.ack_remote_delivery(delivery.id, revision=delivery.revision,
                        scope_hash=delivery.scope_hash, sender_app_id=grant.evidence.app_id,
                        message_id=mid, receipt_hash=receipt_hash, now=self.clock()):
                    raise _Pending('authorization')
            return OutletOutcome('acked', source_id=event['id'], message_id=mid)
        except _Pending as exc:
            return OutletOutcome('pending', exc.reason, source_id=source, notice=NOTICE)
        except Exception:
            return OutletOutcome('pending', 'readback', source_id=source, notice=NOTICE)

    async def deliver(self, event, target_id):
        async with self._lock():
            return await self._deliver(event, target_id)

    async def drain(self, channel, target_id):
        acked, pending, until = 0, 0, 0
        async with self._lock():
            try:
                grant = self.store.remote_grant(target_id)
                if grant is None or channel != grant.evidence.channel_id: raise _Pending('authorization')
                await self._fresh(grant)
                until = self.clock()
                rows = await self.reader.query([{'kinds': [9, 7, 5, 40003], 'authors': [self.record.pubkey],
                    '#h': [channel], 'since': self.store.remote_replay_since(target_id), 'until': until, 'limit': 257}])
                if len(rows) >= 256: raise _Pending('incomplete')
                for event in sorted(rows, key=lambda row: (row['created_at'], row['id'])):
                    result = await self._deliver(event, target_id)
                    if result.status == 'acked': acked += 1
                    elif not (event.get('kind') == 40003
                              and result.status == 'ignored'
                              and result.reason == 'superseded'
                              and result.source_id == event.get('id')):
                        pending += 1
                if pending: raise _Pending('backlog')
                await self._fresh(grant)
                if not self.store.commit_remote_cursor(target_id, until, revision=grant.revision,
                        scope_hash=grant.scope_hash, complete=True, now=self.clock()): raise _Pending('backlog')
                return DrainOutcome('complete', acked_count=acked, scanned_until=until)
            except _Pending as exc:
                return DrainOutcome('pending', exc.reason, acked, max(1, pending), until, NOTICE)
            except Exception:
                return DrainOutcome('pending', 'readback', acked, max(1, pending), until, NOTICE)
