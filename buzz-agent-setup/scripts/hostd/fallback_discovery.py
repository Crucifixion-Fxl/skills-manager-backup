"""Read-only public bot discovery before an owner approval card exists.

Only the issuer's protected local files are used. Public subject identities are
not classified as local or foreign and need not already belong to the channel.
Candidates are short-lived metadata, never approval, membership or readiness.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time

import buzz_feishu_group_sync as gs
import recovery_authority as authority
from . import remote_approval as approval
from .async_io import thread_call
from .native_scopes import parse_bot_scope_envelope
from .bot_clients import BotLarkCli, READ_SCOPE_GROUPS
from .join_effects import Nip98Relay, legacy
from .safety import read_owned
from .signed_reads import SignedReader
from .store import Store

NOTICE = ('群内 bot 的公开身份尚未完成发现核验，保留待处理。怎么解决：检查本机同步应用、完整成员与签名读取、当前频道角色及胜出的绑定认领；不要读取其他机器的凭据。'
          '\n复制给 AI：帮我核查 hostd 群内 bot 发现证据；发现候选不等于批准、授权、卡片就绪或已接入，不要输出凭据或个人资料。')


class _Pending(ValueError):
    def __init__(self): super().__init__(NOTICE)


def _need(value):
    if not value: raise _Pending()


@dataclass(frozen=True)
class FallbackCandidate:
    subject_pubkey: str
    subject_owner_pubkey: str
    subject_app_id: str
    issuer_app_id: str
    binding_id: str
    channel_id: str
    chat_ref: str
    mirror_pubkey: str
    mirror_owner_pubkey: str
    claimed_at: int
    proof_hashes: tuple[str, ...]
    checked_at: int
    expires_at: int


@dataclass(frozen=True)
class DiscoveryResult:
    status: str
    candidates: tuple[FallbackCandidate, ...] = ()
    notice: str = ''


@dataclass(frozen=True)
class _Local:
    issuer: str
    owner: str
    files_hash: str


@dataclass(frozen=True)
class _Identity:
    pubkey: str
    owner: str
    app: str
    profile_id: str
    policy_id: str


@dataclass(frozen=True)
class _Snapshot:
    scope: approval.ApprovalScope
    heartbeat: int
    identities: tuple[_Identity, ...]
    proof_hashes: tuple[str, ...]


class FallbackDiscoverer:
    def __init__(self, store, clients, relay, *, clock=lambda: int(time.time())):
        _need(isinstance(store, Store) and isinstance(relay, Nip98Relay))
        self.store, self.clients, self.relay, self.clock = store, clients, relay, clock
        self.reader = SignedReader(relay)

    def _now(self):
        now = self.clock()
        _need(type(now) is int and 0 <= now < 2**63)
        return now

    def _sql(self, binding_id):
        rows = [row for row in self.store.bindings() if row['binding_id'] == binding_id]
        _need(len(rows) == 1 and rows[0]['status'] == 'active')
        row = rows[0]
        _need(approval._hex(row['mirror_pubkey']) and approval._app(row['sync_app_id'])
              and gs.UUID_RE.fullmatch(row['channel_id']) and gs.CHAT_ID_RE.fullmatch(row['chat_id'])
              and (not row['chat_ref'] or row['chat_ref'] == gs.chat_ref(row['chat_id'])))
        return {key: row[key] for key in ('binding_id', 'channel_id', 'chat_id', 'sync_app_id',
            'config_path', 'config_dir', 'data_dir', 'mirror_pubkey', 'chat_ref', 'status')}

    def _guard(self, sql):
        # Owning SQLite loop only, after every dispatched IO has been reaped.
        _need(self._sql(sql['binding_id']) == sql)

    @staticmethod
    def _path(value):
        _need(isinstance(value, str) and Path(value).is_absolute() and '..' not in Path(value).parts)
        return Path(value)

    def _read_local(self, sql):
        """Protected local file/crypto IO only; no SQL or subject file access."""
        raw = read_owned(self._path(sql['config_path']))
        cfg = approval._json(raw.decode())
        _need((cfg.get('channel_id'), cfg.get('chat_id'), cfg.get('mirror_pubkey')) ==
              (sql['channel_id'], sql['chat_id'], sql['mirror_pubkey']) and gs.binding_claim_enabled(cfg))
        issuer, agents = cfg.get('desk_pubkey'), cfg.get('agents')
        _need(approval._hex(issuer) and isinstance(agents, dict))
        selected = [(pub, entry) for pub, entry in agents.items()
                    if isinstance(entry, dict) and entry.get('app_id') == sql['sync_app_id']]
        _need(len(selected) == 1 and selected[0][0] == issuer)
        entry = selected[0][1]
        config, data = self._path(entry.get('lark_config_dir')), self._path(entry.get('lark_data_dir'))
        _need((str(config), str(data)) == (sql['config_dir'], sql['data_dir']))
        client = self.clients.get(sql['sync_app_id'])
        _need(isinstance(client, BotLarkCli) and (client.app_id, client.config_dir, client.data_dir) ==
              (sql['sync_app_id'], config, data))
        profile_raw = read_owned(config / 'config.json')
        _need(client.identity() == (sql['sync_app_id'], ''))
        people = cfg.get('people_api')
        _need(isinstance(people, dict) and self._path(people.get('signer_env_file')) == self.relay.owner_env_file)
        owner_raw = read_owned(self.relay.owner_env_file)
        mirror_path = self._path(cfg.get('mirror_env_file'))
        mirror_raw = read_owned(mirror_path)
        owner_env, mirror_env = legacy.parse_env(owner_raw.decode()), legacy.parse_env(mirror_raw.decode())
        owner_key = gs.secret_hex(owner_env.get('BUZZ_PRIVATE_KEY'), 'owner')
        mirror_key = gs.secret_hex(mirror_env.get('BUZZ_PRIVATE_KEY'), 'mirror')
        _need(owner_key == self.relay.key and gs._signer_pubkey(owner_key) == self.relay.owner
              and gs._signer_pubkey(mirror_key) == sql['mirror_pubkey'] and self.relay.owner != sql['mirror_pubkey']
              and all(gs.relay_query_url(env.get('BUZZ_RELAY_URL')) == self.relay.origin + '/query'
                      for env in (owner_env, mirror_env)))
        files = [(str(path), hashlib.sha256(value).hexdigest()) for path, value in (
            (sql['config_path'], raw), (config / 'config.json', profile_raw),
            (self.relay.owner_env_file, owner_raw), (mirror_path, mirror_raw))]
        digest = hashlib.sha256(json.dumps(files, separators=(',', ':')).encode()).hexdigest()
        return _Local(issuer, self.relay.owner, digest)

    async def _local(self, sql, previous=None):
        local = await thread_call(self._read_local, sql)
        self._guard(sql)
        if previous is not None: _need(local == previous)
        return local

    async def _query(self, sql, filters):
        values = await self.reader.read('query', filters=[dict(filters, limit=257)])
        self._guard(sql)
        _need(isinstance(values, list) and len(values) < 256
              and len({event['id'] for event in values}) == len(values))
        now = self._now()
        for event in values:
            _need(event['created_at'] <= now + gs.RELAY_CLOCK_SKEW_SECONDS)
            for key, expected in filters.items():
                if key == 'kinds': _need(event['kind'] in expected)
                elif key == 'authors': _need(event['pubkey'] in expected)
                elif key == '#d':
                    _need(len(authority.tags(event, 'd')) == 1
                          and len(authority.tags(event, 'd')[0]) == 2
                          and authority.tags(event, 'd')[0][1] in expected)
                else: raise _Pending()
        return values

    async def _snapshot(self, sql, local):
        policies = await self.reader.read('policy_snapshot')
        self._guard(sql)
        now = self._now()
        def policy_subjects():
            pubs = {local.issuer, sql['mirror_pubkey']}
            for event in policies:
                tag = authority.tags(event, 'd')
                _need(len(tag) == 1 and len(tag[0]) == 2 and approval._hex(tag[0][1]))
                block = approval._policy(event, tag[0][1], now).get('feishu')
                if isinstance(block, dict) and block.get('mirror') is True:
                    _need('app_id' not in block)
                pubs.add(tag[0][1])
            _need(len(pubs) <= 1000)
            return sorted(pubs)
        pubs = await thread_call(policy_subjects)
        self._guard(sql)
        profiles = []
        for group in gs.batches(pubs, 64):
            profiles.extend(await self._query(sql, {'kinds': [0], 'authors': group}))
        rosters = await self._query(sql, {'kinds': [39002], 'authors': [self.relay.relay_pubkey], '#d': [sql['channel_id']]})
        now = self._now()
        def validate():
            groups = {}
            for event in profiles:
                approval._owner(event, event['pubkey'], now)
                groups.setdefault(event['pubkey'], []).append(event)
            heads = {pub: authority.latest(rows) for pub, rows in groups.items()}
            _need(local.issuer in heads and sql['mirror_pubkey'] in heads)
            identities = []
            own_policy = None
            for pub, profile in sorted(heads.items()):
                owner = approval._owner(profile, pub, now)
                policy = authority.latest([e for e in policies if e['pubkey'] == owner
                                           and authority.tags(e, 'd') == [['d', pub]]])
                if pub == local.issuer: own_policy = policy
                if policy is None: continue
                block = approval._policy(policy, pub, now).get('feishu')
                if isinstance(block, dict) and block.get('mirror') is not True and approval._app(block.get('app_id')):
                    identities.append(_Identity(pub, owner, block['app_id'], profile['id'], policy['id']))
            mirror_profile = heads[sql['mirror_pubkey']]
            _need(approval._owner(mirror_profile, sql['mirror_pubkey'], now) == local.owner)
            mirror_policy = authority.latest([e for e in policies if e['pubkey'] == local.owner
                and authority.tags(e, 'd') == [['d', sql['mirror_pubkey']]]])
            _need(mirror_policy is not None and own_policy is not None)
            block = approval._policy(mirror_policy, sql['mirror_pubkey'], now).get('feishu')
            _need(isinstance(block, dict) and 'app_id' not in block and isinstance(block.get('bindings'), list))
            entries = [e for e in block['bindings'] if isinstance(e, dict)
                       and (e.get('channel'), e.get('chat_ref')) == (sql['channel_id'], gs.chat_ref(sql['chat_id']))]
            _need(len(entries) == 1)
            scope = approval.ApprovalScope(local.issuer, approval._owner(heads[local.issuer], local.issuer, now),
                sql['sync_app_id'], sql['channel_id'], gs.chat_ref(sql['chat_id']), sql['mirror_pubkey'],
                local.owner, entries[0].get('claimed_at'))
            for event in rosters:
                approval._event(event, 39002, now)
                authority.membership(event, sql['channel_id'])
            context = approval.ProofContext(scope, self.relay.relay_pubkey, heads[local.issuer], own_policy,
                tuple(profiles), tuple(policies), authority.latest(rosters), now, complete=True)
            # Here completeness comes from the actual bounded whole-packet
            # queries. Use the issuer as the required roster bot; a discovered
            # public subject does not borrow that post-approval permission.
            claim, _, policy_id = approval._current(context)
            _need(approval.sync_app(context).app_id == sql['sync_app_id'])
            proof = (heads[local.issuer]['id'], own_policy['id'], mirror_profile['id'], policy_id, context.roster['id'])
            return _Snapshot(scope, claim.heartbeat, tuple(identities), proof)
        value = await thread_call(validate)
        self._guard(sql)
        return value

    def _bot_members(self, sql):
        client = self.clients[sql['sync_app_id']]
        _need(client.identity() == (sql['sync_app_id'], ''))
        payload = client.call('scopes', ['api', 'GET', '/open-apis/application/v6/scopes', '--as', 'bot'], full=True)
        granted = parse_bot_scope_envelope(payload)
        _need(all(granted & alternatives for alternatives in READ_SCOPE_GROUPS.values()))
        listing = client.member_listing(sql['chat_id'], 'union_id')
        _need(isinstance(listing, gs.MemberListing) and listing.complete is True
              and sql['sync_app_id'] in listing.bots)
        return tuple(sorted(listing.bots))

    async def discover(self, binding_id):
        try:
            started = self._now()
            sql = self._sql(binding_id)
            local = await self._local(sql)
            before = await self._snapshot(sql, local)
            apps = await thread_call(self._bot_members, sql)
            self._guard(sql)
            after = await self._snapshot(sql, local)
            _need(after.scope == before.scope and after.heartbeat >= before.heartbeat)
            # Local protected-file IO is reaped before the final owning-loop
            # SQL guard. No await or external mutation follows this guard.
            await self._local(sql, local)
            now = self._now()
            _need(started <= now <= started + 30)
            expires = min(now + 30, after.heartbeat + gs.CLAIM_LEASE_SECONDS)
            _need(expires > now)
            candidates = []
            for app in apps:
                if app == sql['sync_app_id']: continue
                identities = [identity for identity in after.identities if identity.app == app]
                _need(len(identities) == 1)
                identity = identities[0]
                _need([(i.pubkey, i.owner, i.app) for i in before.identities if i.app == app]
                      == [(identity.pubkey, identity.owner, identity.app)])
                candidates.append(FallbackCandidate(identity.pubkey, identity.owner, app, sql['sync_app_id'],
                    binding_id, sql['channel_id'], gs.chat_ref(sql['chat_id']), sql['mirror_pubkey'],
                    local.owner, after.scope.claimed_at, after.proof_hashes + (identity.profile_id, identity.policy_id),
                    now, expires))
            self._guard(sql)
            return DiscoveryResult('complete', tuple(candidates))
        except Exception:
            return DiscoveryResult('pending', notice=NOTICE)
