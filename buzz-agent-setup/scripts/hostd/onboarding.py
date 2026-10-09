"""Durable card-only approval coordinator. Outward effects remain explicit adapters.

Root integration supplies fresh authenticated people/profile readers and runs run().
Effect adapters MUST be request-ID idempotent; readback is authoritative after restart.
The callback process only queues typed metadata and returns toast synchronously.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import hashlib
import inspect
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs
try:
    from .store import hexid, ident, stamp
    from .async_io import thread_call
except ImportError:
    from store import hexid, ident, stamp
    from async_io import thread_call

NOTICE = ("接入申请暂时无法继续。怎么解决：下一步请检查该 agent 的应用身份、事件订阅、人员 union_id 绑定与本机接入任务，再重试当前卡片。"
          "\n复制给 AI：帮我核查 hostd 卡片接入的应用身份、owner 人员绑定、投递账本及后台开通读回结果；不要输出凭据、卡片 token 或消息正文。")
QUEUED = {"toast": {"type": "info", "content": "操作已收到并排队核验；只有 owner 的点击会生效"}}


def _id(value, prefix):
    ident(value, prefix)
    if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9]+", value):
        raise ValueError(NOTICE)


def _request(value):
    if not isinstance(value, str) or not re.fullmatch(r"JOIN-[0-9a-f]{8}", value):
        raise ValueError(NOTICE)


@dataclass(frozen=True, init=False)
class PeopleBindings:
    """Immutable hostd identity projection, without legacy module type coupling."""
    _bindings: tuple[tuple[str, str], ...] = field(repr=False)

    def __init__(self, union_ids):
        try:
            if not isinstance(union_ids, dict):
                raise ValueError
            for pubkey, union_id in union_ids.items():
                hexid(pubkey); _id(union_id, 'on_')
            if len(set(union_ids.values())) != len(union_ids):
                raise ValueError
            object.__setattr__(self, '_bindings', tuple(sorted(union_ids.items())))
        except Exception:
            raise ValueError(NOTICE) from None

    @property
    def union_ids(self):
        return dict(self._bindings)


@dataclass(frozen=True)
class Operator:
    open_id: str
    union_id: str = ""

    def __post_init__(self):
        _id(self.open_id, "ou_")
        if self.union_id:
            _id(self.union_id, "on_")


@dataclass(frozen=True)
class Person:
    app_id: str
    union_id: str
    pubkey: str
    checked_at: int

    def __post_init__(self):
        _id(self.app_id, "cli_"); _id(self.union_id, "on_"); hexid(self.pubkey); stamp(self.checked_at)


@dataclass(frozen=True)
class Invite:
    app_id: str
    agent_id: str
    chat_id: str
    event_id: str
    operator: Operator
    kind: str
    binding_id: str | None = None

    def __post_init__(self):
        _id(self.app_id, "cli_"); hexid(self.agent_id); _id(self.chat_id, "oc_"); ident(self.event_id)
        if not isinstance(self.operator, Operator) or self.kind not in ("channel", "new_binding"):
            raise ValueError(NOTICE)
        if self.binding_id is not None:
            ident(self.binding_id)


@dataclass(frozen=True)
class CardDecision:
    app_id: str
    chat_id: str
    message_id: str
    event_id: str
    request_id: str
    generation: int
    approved: bool
    operator: Operator

    def __post_init__(self):
        _id(self.app_id, "cli_"); _id(self.chat_id, "oc_"); _id(self.message_id, "om_"); ident(self.event_id)
        _request(self.request_id); stamp(self.generation)
        if type(self.approved) is not bool or self.generation < 1 or not isinstance(self.operator, Operator):
            raise ValueError(NOTICE)


def card_from_feed(row):
    """Decode only trusted SDK feed context; button-supplied app/message are ignored."""
    try:
        if not isinstance(row, dict) or row.get("type") != "card.action.trigger":
            raise ValueError(NOTICE)
        operator, context, value = row["operator"], row["context"], row["action"]["value"]
        if not all(isinstance(item, dict) for item in (operator, context, value)) or value.get("decision") not in ("approve", "deny"):
            raise ValueError(NOTICE)
        return CardDecision(row["app"], context["open_chat_id"], context["open_message_id"], row["event_id"],
                            value["request_id"], value["generation"], value["decision"] == "approve",
                            Operator(operator["open_id"], operator.get("union_id", "")))
    except (KeyError, TypeError, ValueError):
        raise ValueError(NOTICE) from None


@dataclass(frozen=True)
class CardRecovery:
    generation: int
    message_id: str
    verified: bool

    def __post_init__(self):
        stamp(self.generation)
        if self.generation < 1 or type(self.verified) is not bool:
            raise ValueError(NOTICE)
        if self.message_id:
            _id(self.message_id, "om_")


@dataclass(frozen=True)
class EffectReceipt:
    request_id: str
    agent_id: str
    chat_id: str
    verified: bool

    def __post_init__(self):
        _request(self.request_id); hexid(self.agent_id); _id(self.chat_id, "oc_")
        if type(self.verified) is not bool:
            raise ValueError(NOTICE)


class IdentityResolver:
    """Actual app-scoped contact reads plus uncached NIP98 people/profile loaders.

    people_loader(app_id, now) returns gs.PeopleAnswer from an authenticated fetch;
    profile_loader(agent_id, now) returns the latest signed relay kind:0 event.
    Both loaders must fail closed on incomplete responses and untrusted origins.
    """
    def __init__(self, clients, people_loader, profile_loader):
        self.clients, self.people_loader, self.profile_loader = clients, people_loader, profile_loader

    def _client(self, app_id):
        client = self.clients.get(app_id)
        if client is None or client.app_id != app_id:
            raise ValueError(NOTICE)
        return client

    def _people(self, app_id, now):
        self._client(app_id)
        answer = self.people_loader(app_id, now)
        return self._validate_people(answer)

    @staticmethod
    def _validate_people(answer):
        if not isinstance(answer, (PeopleBindings, gs.PeopleAnswer)) or not isinstance(answer.union_ids, dict):
            raise ValueError(NOTICE)
        for pubkey, union_id in answer.union_ids.items():
            hexid(pubkey); _id(union_id, "on_")
        if len(set(answer.union_ids.values())) != len(answer.union_ids):
            raise ValueError(NOTICE)
        return answer.union_ids

    def _operator_union(self, app_id, operator):
        client = self._client(app_id)
        data = client.call("join operator identity", ["api", "GET", f"/open-apis/contact/v3/users/{operator.open_id}",
                          "--params", json.dumps({"user_id_type": "open_id"}), "--as", "bot"])
        user = data.get("user") if isinstance(data, dict) else None
        if not isinstance(user, dict) or user.get("open_id") != operator.open_id:
            raise ValueError(NOTICE)
        union = user.get("union_id")
        _id(union, "on_")
        if operator.union_id and union != operator.union_id:
            raise ValueError(NOTICE)
        return union

    async def inviter(self, app_id, operator, *, now):
        # An invitation creates a request, so no Buzz people binding is required.
        return await thread_call(self._operator_union, app_id, operator)

    async def _load(self, loader, *args):
        if inspect.iscoroutinefunction(loader):
            return await loader(*args)
        return await thread_call(loader, *args)

    async def _people_async(self, app_id, now):
        self._client(app_id)
        return self._validate_people(await self._load(self.people_loader, app_id, now))

    async def resolve(self, app_id, operator, *, now):
        union = await thread_call(self._operator_union, app_id, operator)
        people = await self._people_async(app_id, now)
        matches = [pk for pk, uid in people.items() if uid == union]
        if len(matches) != 1:
            raise ValueError(NOTICE)
        return Person(app_id, union, matches[0], now)

    async def owner_union(self, owner_pubkey, app_id, *, now):
        union = (await self._people_async(app_id, now)).get(owner_pubkey)
        _id(union, "on_")
        return union

    async def owner(self, agent_id, app_id, *, now):
        self._client(app_id)
        event = await self._load(self.profile_loader, agent_id, now)
        if not gs._nip01_event_verified(event) or event.get("kind") != 0 or event.get("pubkey") != agent_id:
            raise ValueError(NOTICE)
        tags = [tag for tag in event["tags"] if tag and tag[0] == "auth"]
        if len(tags) != 1 or len(tags[0]) != 4 or tags[0][2] != "":
            raise ValueError(NOTICE)
        owner, sig = tags[0][1], tags[0][3]
        hexid(owner)
        if not isinstance(sig, str) or not re.fullmatch(r"[0-9a-f]{128}", sig):
            raise ValueError(NOTICE)
        digest = hashlib.sha256(f"nostr:agent-auth:{agent_id}:".encode()).digest()
        if not gs.sync.nk.schnorr_verify(digest, bytes.fromhex(owner), bytes.fromhex(sig)):
            raise ValueError(NOTICE)
        return owner


class Coordinator:
    def __init__(self, store, identity, cards, *, effects=None, queue_limit=128, clock=time.time):
        if type(queue_limit) is not int or not 1 <= queue_limit <= 4096:
            raise ValueError(NOTICE)
        self.store, self.identity, self.cards, self.effects = store, identity, cards, effects
        self.queue_limit = queue_limit
        self.clock = clock
        self._queue = deque()
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._closed = False
        self.last_notice = ""

    def _enqueue(self, value, now):
        stamp(now)
        if self._closed or len(self._queue) >= self.queue_limit:
            return {"toast": {"type": "error", "content": NOTICE}}
        self._queue.append((value, now))
        self._wake.set()
        return {"toast": dict(QUEUED["toast"])}

    def enqueue_invite(self, invite, *, now):
        if not isinstance(invite, Invite):
            raise ValueError(NOTICE)
        return self._enqueue(invite, now)

    def enqueue_card(self, decision, *, now):
        if not isinstance(decision, CardDecision):
            raise ValueError(NOTICE)
        if self.store.fallback_provenance(decision.request_id) is not None:
            return {"toast": {"type": "info", "content": NOTICE}}
        # This cheap rejection is visible only to the clicking operator, no IO.
        row = self.store.join_request(decision.request_id)
        if not row or (row["callback_app_id"], row["chat_id"], row["card_message_id"], row["card_generation"]) != (decision.app_id, decision.chat_id, decision.message_id, decision.generation):
            return {"toast": {"type": "error", "content": "这张卡片已失效或不属于本群。怎么解决：下一步请 owner 使用当前申请卡片。复制给 AI：帮我核查接入申请当前卡片与应用，不要输出凭据。"}}
        return self._enqueue(decision, now)

    async def _person(self, app, operator, now):
        person = await self.identity.resolve(app, operator, now=now)
        if not isinstance(person, Person) or person.app_id != app or not 0 <= now - person.checked_at <= 60:
            raise ValueError(NOTICE)
        if operator.union_id and operator.union_id != person.union_id:
            raise ValueError(NOTICE)
        return person

    async def _invite(self, invite, now):
        agent = self.store.conn.execute("SELECT * FROM agent WHERE pubkey=? AND status='active'", (invite.agent_id,)).fetchone()
        if not agent or agent["app_id"] != invite.app_id:
            return
        owner = await self.identity.owner(invite.agent_id, invite.app_id, now=now)
        if owner != agent["owner_pubkey"]:
            raise ValueError(NOTICE)
        union = await self.identity.inviter(invite.app_id, invite.operator, now=now)
        _id(union, "on_")
        if invite.operator.union_id and union != invite.operator.union_id:
            raise ValueError(NOTICE)
        request_id = "JOIN-" + hashlib.sha256(f"hostd-join:v1:{invite.app_id}:{invite.event_id}:{invite.agent_id}:{invite.chat_id}".encode()).hexdigest()[:8]
        with self.store.transaction():
            row = self.store.join_request(request_id)
            if row is None:
                active = [r for r in self.store.join_requests() if r["agent_id"] == invite.agent_id and r["chat_id"] == invite.chat_id and r["status"] in ("requested", "approved", "applied")]
                row = active[0] if active else self.store.create_join(request_id, invite.agent_id, owner, invite.app_id, invite.chat_id,
                                                                    kind=invite.kind, binding_id=invite.binding_id, now=now)
        await self._send(row, now)

    async def _send(self, row, now):
        previous = self.store.join_transport(row["request_id"])
        generation = self.store.reserve_join_card(row["request_id"], now=now)
        if generation is None:
            return
        sending = False
        try:
            message_id = ""
            if previous and previous["send_status"] in ("unknown", "reserved"):
                recovery = await self.cards.recover(row, generation) if hasattr(self.cards, "recover") else None
                if not isinstance(recovery, CardRecovery) or recovery.generation != generation or not recovery.verified:
                    raise ValueError(NOTICE)
                message_id = recovery.message_id
            if not message_id:
                sending = True
                message_id = await self.cards.send(row, generation)
            if self.store.finish_join_card(row["request_id"], generation, message_id, now=max(now, int(self.clock()))):
                await self._flush_notices(now,request_id=row['request_id'])
        except Exception as exc:
            definite = sending and isinstance(exc, gs.CliError) and exc.definite is True
            self.store.fail_join_card(row["request_id"], generation, definite=definite, now=max(now, int(self.clock())))
            self.last_notice = NOTICE
        transport = self.store.join_transport(row["request_id"])
        if transport and (transport["attempts"] > 1 or transport["send_status"] in ("failed", "unknown")):
            union = await self.identity.owner_union(row["owner_pubkey"], row["callback_app_id"], now=now)
            _id(union, "on_")
            if self.store.reserve_join_dm(row["request_id"], now=now):
                try:
                    await self.cards.dm(row, union)
                except Exception:
                    self.last_notice = NOTICE

    async def _card(self, decision, now):
        if self.store.fallback_provenance(decision.request_id) is not None:
            return
        row = self.store.join_request(decision.request_id)
        if not row or row["status"] != "requested" or row["chat_id"] != decision.chat_id:
            return
        owner = await self.identity.owner(row["agent_id"], decision.app_id, now=now)
        if owner != row["owner_pubkey"]:
            raise ValueError(NOTICE)
        try:
            person = await self._person(decision.app_id, decision.operator, now)
        except Exception:
            union = await self.identity.inviter(decision.app_id, decision.operator, now=now)
            await self._refuse(row, union, decision.event_id, "identity", now)
            return
        now = max(now, int(self.clock()))
        if not self.store.decide_join(row["request_id"], decision.event_id, person.pubkey, decision.app_id,
                                     decision.message_id, decision.generation, approved=decision.approved, now=now):
            await self._refuse(row, person.union_id, decision.event_id, "owner" if person.pubkey != row["owner_pubkey"] else "card", now)
            return
        row = self.store.join_request(row["request_id"])
        if row["status"] == "denied":
            try:
                await self._present(row, "denied", now)
            finally:
                await self._cleanup(row)
        else:
            await self._apply(row, now)

    def _verified(self, receipt, row):
        return isinstance(receipt, EffectReceipt) and receipt.verified is True and (receipt.request_id, receipt.agent_id, receipt.chat_id) == (row["request_id"], row["agent_id"], row["chat_id"])

    async def _apply(self, row, now):
        try:
            await self._apply_verified(row, now)
        except Exception:
            self.last_notice = NOTICE
            current = self.store.join_request(row["request_id"])
            await self._present(current, "done" if current["status"] == "done" else "blocked", now)

    async def _apply_verified(self, row, now):
        if self.effects is None:
            await self._present(row, "blocked", now)
            return
        receipt = await self.effects.readback(row)
        if not self._verified(receipt, row) and row["status"] == "approved":
            await self.effects.apply(row)
            receipt = await self.effects.readback(row)
        if not self._verified(receipt, row):
            await self._present(row, "blocked", now)
            return
        if row["status"] == "approved":
            self.store.advance_join(row["request_id"], expected="approved", target="applied", now=now)
        if self.store.advance_join(row["request_id"], expected="applied", target="done", now=now):
            await self._present(self.store.join_request(row["request_id"]), "done", now)

    async def _refuse(self, row, union_id, event_id, reason, now):
        _id(union_id, "on_")
        if self.store.reserve_join_feedback(row["request_id"], row["callback_app_id"], event_id, union_id, reason=reason, now=now):
            sent = False
            try:
                await self.cards.refuse(row, union_id, event_id, reason)
                sent = True
            except Exception:
                self.last_notice = NOTICE
            finally:
                self.store.finish_join_feedback(row["callback_app_id"], event_id, sent=sent, now=max(now, int(self.clock())))

    async def _present(self, row, state, now):
        if row["card_message_id"]:
            self.store.queue_join_notice(row["request_id"], row["card_message_id"], state, now=max(now, int(self.clock())))
            await self._flush_notices(now,request_id=row['request_id'])

    async def retry_notices(self, *, now):
        """One persisted card replacement, without approval or effect replay."""
        stamp(now)
        async with self._lock:
            if not self._closed:await self._flush_notices(now, limit=1)

    async def _flush_notices(self, now, *, request_id=None, limit=None):
        attempted = 0
        for notice in self.store.join_notices():
            if request_id is not None and notice['request_id']!=request_id:continue
            if limit is not None and attempted >= limit:break
            row = self.store.join_request(notice["request_id"])
            if row is None or self.store.fallback_provenance(row["request_id"]) is not None:continue
            # A stale notice cannot overwrite a newer generation or restore an
            # earlier approval state. Superseded cards keep their own target.
            expected = {"approved": ("approved", "applied"), "blocked": ("approved", "applied"),
                        "done": ("done",), "denied": ("denied",), "expired": ("expired",)}
            if notice["state"] == "superseded":
                if notice["message_id"] == row["card_message_id"]:continue
            elif (notice["message_id"] != row["card_message_id"]
                  or row["status"] not in expected.get(notice["state"], ())):continue
            # Prior network/effect awaits may have advanced the durable row's
            # timestamp beyond the caller's clock sample. Keep Store CAS intact.
            now = max(now, int(self.clock()))
            state = self.store.reserve_join_notice(notice["request_id"], notice["message_id"], now=now)
            if state is None:
                continue
            attempted += 1
            sent = False
            try:
                await self.cards.update(row, notice["message_id"], state)
                sent = True
            except Exception:
                self.last_notice = NOTICE
            finally:
                self.store.finish_join_notice(notice["request_id"], notice["message_id"], state, sent=sent, now=max(now, int(self.clock())))

    async def _cleanup(self, row):
        if self.effects is not None:
            await self.effects.cleanup(row)

    async def drain(self):
        async with self._lock:
            while self._queue:
                item, now = self._queue.popleft()
                try:
                    now = max(now, int(self.clock()))
                    await (self._invite(item, now) if isinstance(item, Invite) else self._card(item, now))
                except Exception:
                    self.last_notice = NOTICE
            self._wake.clear()

    async def tick(self, *, now, request_id=None):
        stamp(now)
        if request_id is not None:_request(request_id)
        now = max(now, int(self.clock()))
        async with self._lock:
            rows = self.store.join_requests() if request_id is None else [self.store.join_request(request_id)]
            for row in rows:
                if row is None:continue
                if self.store.fallback_provenance(row["request_id"]) is not None:
                    continue
                try:
                    if row["status"] == "requested":
                        if now >= row["deadline"]:
                            if self.store.advance_join(row["request_id"], expected="requested", target="expired", now=now):
                                try:
                                    if row["card_message_id"]:
                                        await self._present(self.store.join_request(row["request_id"]), "expired", now)
                                finally:
                                    await self._cleanup(self.store.join_request(row["request_id"]))
                        else:
                            await self._send(row, now)
                    elif row["status"] in ("approved", "applied"):
                        await self._apply(row, now)
                    elif row["status"] in ("denied", "expired"):
                        await self._present(row, row["status"], now)
                        await self._cleanup(row)
                    elif row["status"] == "done":
                        await self._present(row, "done", now)
                except Exception:
                    self.last_notice = NOTICE
            await self._flush_notices(now,request_id=request_id)

    async def run(self):
        while not self._closed:
            await self.drain()
            await self.tick(now=int(time.time()))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    def close(self):
        self._closed = True
        self._wake.set()
