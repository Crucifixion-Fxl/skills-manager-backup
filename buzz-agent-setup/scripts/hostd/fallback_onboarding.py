"""Local sync-bot cards for public subjects, without foreign runtime authority.

The durable marker is metadata only. A decision is not a grant, publication or
activation. UNKNOWN card attempts are recovered by original native GET only.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import time

import recovery_authority as authority
from . import agent_catalog, onboarding
from .async_io import thread_call
from .fallback_discovery import FallbackDiscoverer
from .join_cards import BotCards, card, _key
from .join_effects import Nip98Relay
from .remote_approval import _owner
from .signed_reads import SignedReader
from .store import FallbackRequestEvidence, Store, hexid, ident, stamp

NOTICE = ('接入申请保留待处理，尚未批准或开通。怎么解决：检查本机同步应用、当前绑定与签名认领、完整群成员、owner 的 union_id 人员绑定和原卡片读回；未知发送结果不要重新发卡。'
          '\n复制给 AI：帮我核查 hostd 兜底卡片的本机目录、原申请、应用和 owner 决策证据；不要读取其他机器的凭据或输出人员资料。')


def _need(value):
    if not value: raise ValueError(NOTICE)


@dataclass(frozen=True)
class FallbackResult:
    status: str
    request_id: str = ''
    notice: str = ''


class _Cards(BotCards):
    """Same native BotCards readback/UUID, with decision-only product wording."""
    async def send(self, row, generation):
        payload = card(row, generation)
        payload['elements'][0]['text']['content'] = (
            '只有该公开 agent 的 owner 能决定。此卡片由本群同步 bot 发出；同意只记录本次接入决定，'
            '后续签名授权和 agent 所在机器的开通仍需分别核验。')
        client = self._client(row)
        message = await thread_call(client.send_card, row['chat_id'],
            json.dumps(payload,ensure_ascii=False,separators=(',',':')), _key(row['request_id'],f'card:{generation}'))
        onboarding._id(message,'om_')
        view = await thread_call(client.message_view,message,'union_id')
        _need(self._matches(view,row,generation))
        return message


class FallbackOnboarding:
    def __init__(self, store, discoverer, issuer_clients, relay, *, catalog_path, legacy_join_path,
                 clock=lambda: int(time.time())):
        _need(isinstance(store,Store) and isinstance(discoverer,FallbackDiscoverer)
              and discoverer.store is store and isinstance(relay,Nip98Relay) and discoverer.relay is relay
              and discoverer.clients is issuer_clients)
        self.store,self.discoverer,self.clients,self.relay = store,discoverer,issuer_clients,relay
        self.catalog_path,self.legacy_join_path,self.clock = catalog_path,legacy_join_path,clock
        self.cards,self.reader = _Cards(issuer_clients),SignedReader(relay)
        self._locks = {}

    def _now(self):
        value = self.clock(); stamp(value); return value

    @staticmethod
    def _scope(candidate):
        return tuple(getattr(candidate,name) for name in (
            'subject_pubkey','subject_owner_pubkey','subject_app_id','issuer_app_id','binding_id',
            'channel_id','chat_ref','mirror_pubkey','mirror_owner_pubkey','claimed_at'))

    async def _fresh(self,binding_id,subject,previous=None):
        started = self._now()
        sql = self.discoverer._sql(binding_id)
        local = await self.discoverer._local(sql)
        discovered = await self.discoverer.discover(binding_id)
        _need(discovered.status == 'complete')
        candidates = [c for c in discovered.candidates if c.subject_pubkey == subject]
        _need(len(candidates) == 1)
        candidate = candidates[0]
        catalog = await thread_call(agent_catalog.load,self.catalog_path,legacy_join_path=self.legacy_join_path)
        catalog_hash = hashlib.sha256((catalog.catalog_sha256+':'+catalog.legacy_join_sha256).encode()).hexdigest()
        # This final fresh discover also reaps protected issuer IO and checks SQL
        # after the protected catalog load. The public subject uses no files.
        discovered = await self.discoverer.discover(binding_id)
        matches = [c for c in discovered.candidates if c.subject_pubkey == subject]
        _need(discovered.status == 'complete' and len(matches) == 1 and self._scope(matches[0]) == self._scope(candidate))
        candidate = matches[0]
        current_catalog = await thread_call(agent_catalog.load,self.catalog_path,legacy_join_path=self.legacy_join_path)
        _need((current_catalog.catalog_sha256,current_catalog.legacy_join_sha256) ==
              (catalog.catalog_sha256,catalog.legacy_join_sha256))
        await self.discoverer._local(sql,local)
        now = self._now()
        _need(started <= now <= started + 30)
        _need(candidate.checked_at <= now < candidate.expires_at)
        if previous is not None:
            _need(self._scope(candidate) == self._scope(previous) and catalog_hash == previous.catalog_hash)
        records = [r for r in catalog.records if r.pubkey == subject or r.app_id == candidate.subject_app_id]
        if records:
            _need(len(records) == 1 and records[0].status == 'own_bot_verified'
                  and (records[0].pubkey,records[0].owner_pubkey,records[0].app_id) ==
                  (subject,candidate.subject_owner_pubkey,candidate.subject_app_id))
        evidence = FallbackRequestEvidence(*self._scope(candidate),catalog_hash,candidate.proof_hashes,
            candidate.checked_at,candidate.expires_at)
        return evidence,bool(records)

    async def _identity(self,row):
        async def people(app,now):
            _need(app == row['callback_app_id'])
            binding = self.discoverer._sql(row['binding_id'])
            values = await self.reader.read('people',channel=binding['channel_id'])
            self.discoverer._guard(binding)
            return onboarding.PeopleBindings(values)
        async def profile(pub,now):
            rows = await self.reader.read('query',filters=[{'kinds':[0],'authors':[pub],'limit':257}])
            _need(isinstance(rows,list) and len(rows)<256 and all(e['pubkey']==pub and e['kind']==0 for e in rows)
                  and len({e['id'] for e in rows}) == len(rows))
            latest = authority.latest(rows); _owner(latest,pub,self._now())
            return latest
        return onboarding.IdentityResolver(self.clients,people,profile)

    async def request(self,binding_id,subject_pubkey,native_event_id):
        request_id = ''
        try:
            ident(binding_id); hexid(subject_pubkey); ident(native_event_id)
            lock = self._locks.setdefault((binding_id,subject_pubkey),asyncio.Lock())
            async with lock:
                evidence,own = await self._fresh(binding_id,subject_pubkey)
                if own: return FallbackResult('own_local')
                request_id = 'JOIN-'+hashlib.sha256(json.dumps(
                    ['hostd-fallback:v1',binding_id,subject_pubkey,native_event_id],separators=(',',':')).encode()).hexdigest()[:8]
                row = self.store.reserve_fallback_join(request_id,evidence,now=self._now())
                request_id = row['request_id']
                marker = self.store.fallback_provenance(request_id)
                if row['card_message_id'] or row['status'] == 'approved':
                    return FallbackResult('requested',request_id)
                evidence,own = await self._fresh(binding_id,subject_pubkey,marker)
                _need(not own)
                generation = self.store.reserve_fallback_card(request_id,evidence,now=self._now())
                if generation is not None:
                    # SQL UNKNOWN is committed before any awaited physical send.
                    message = await self.cards.send(row,generation)
                else:
                    transport = self.store.join_transport(request_id)
                    _need(transport is not None and transport['send_status'] == 'unknown')
                    generation = transport['send_generation']
                    recovery = await self.cards.recover(row,generation)
                    _need(isinstance(recovery,onboarding.CardRecovery) and recovery.verified is True
                          and recovery.generation == generation and recovery.message_id)
                    message = recovery.message_id
                evidence,own = await self._fresh(binding_id,subject_pubkey,marker)
                _need(not own and self.store.finish_fallback_card(request_id,generation,message,evidence,now=self._now()))
                return FallbackResult('requested',request_id)
        except Exception:
            return FallbackResult('pending',request_id,NOTICE)

    async def decide(self,decision):
        request_id = decision.request_id if isinstance(decision,onboarding.CardDecision) else ''
        try:
            _need(isinstance(decision,onboarding.CardDecision))
            marker,row = self.store.fallback_provenance(request_id),self.store.join_request(request_id)
            _need(marker is not None and row is not None and row['status']=='requested'
                  and self._now()<row['deadline'] and
                  (decision.app_id,decision.chat_id,decision.message_id,decision.generation) ==
                  (row['callback_app_id'],row['chat_id'],row['card_message_id'],row['card_generation']))
            async with self._locks.setdefault((marker.binding_id,marker.subject_pubkey),asyncio.Lock()):
                evidence,own = await self._fresh(marker.binding_id,marker.subject_pubkey,marker)
                _need(not own)
                identity = await self._identity(row)
                owner = await identity.owner(marker.subject_pubkey,marker.issuer_app_id,now=self._now())
                person = await identity.resolve(marker.issuer_app_id,decision.operator,now=self._now())
                _need(owner == marker.subject_owner_pubkey == person.pubkey and person.app_id == marker.issuer_app_id)
                view = await thread_call(self.clients[marker.issuer_app_id].message_view,decision.message_id,'union_id')
                _need(self.cards._matches(view,row,decision.generation))
                evidence,own = await self._fresh(marker.binding_id,marker.subject_pubkey,marker)
                _need(not own and self.store.decide_fallback_join(request_id,decision.event_id,person.pubkey,
                    decision.app_id,decision.message_id,decision.generation,evidence,approved=decision.approved,now=self._now()))
                return FallbackResult('approved' if decision.approved else 'denied',request_id)
        except Exception:
            return FallbackResult('pending',request_id,NOTICE)
