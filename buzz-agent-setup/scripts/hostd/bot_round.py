"""Hostd identity and bot-reader boundary; legacy Round remains unchanged.

Configuration is validated by the existing loader before this adapter is used.
The private runtime copy reinterprets the legacy owner_app_id protection slot
as the selected sync bot, and removes the former app-scoped human open_id.
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs
from buzz_agent_join_requests import parse_env
from recovery_relay import RecoveryRelay
try:
    from .bot_clients import BotLarkCli, READ_SCOPE_GROUPS, failure
    from . import claim_sync_app as sync_app_claim
    from .signed_reads import SignedReader
    from .safety import read_owned
except ImportError:
    from bot_clients import BotLarkCli, READ_SCOPE_GROUPS, failure
    import claim_sync_app as sync_app_claim
    from signed_reads import SignedReader
    from safety import read_owned


def runtime_config(validated):
    if gs.identity_mode(validated) != 'union_id':
        raise failure('hostd 暂不支持旧 email 身份模式', '先完成已验证的 union_id 映射迁移；不要复用其他应用的 open_id')
    cfg = copy.deepcopy(dict(validated))
    sync = cfg['agents'].get(cfg['desk_pubkey'])
    if not sync:
        raise failure('没有选定同步 bot', '为绑定指定一个本机已配置的 agent bot')
    cfg['owner_app_id'] = sync['app_id']  # The existing membership planner protects the sync app.
    cfg.pop('owner_open_id', None)  # No silent cross-app human identity reinterpretation.
    cfg['identity'] = 'union_id'
    return cfg


class HostdRound(gs.Round):
    def _feishu_overlap(self):
        return 900

    def _feishu_bot_apps(self):
        # Native GET represents selected bot mentions with id_type=app_id,
        # independently of the requested human identity projection. Only
        # currently admitted channel agents with a real bot roster entry may
        # supply an alias; names and a bare cli_ string never identify a bot.
        agents = self.agents_in_channel()
        candidates = [(pk, self.cfg['agents'][pk]['app_id'])
                      for pk in agents & self.verified_agents if pk in self.cfg['agents']]
        if not self.agent_lookup_failed:
            candidates.extend((pk, app) for pk, app in self.directory.items() if pk in agents)
        by_app = {}
        for pubkey, app in candidates:
            if app in self.bot_members:
                by_app.setdefault(app, set()).add(pubkey)
        return {app: next(iter(pubkeys)) for app, pubkeys in by_app.items() if len(pubkeys) == 1}

    def __init__(self, cfg, clients, *args, reader_namespace=None, claim_relay_pubkey=None, **kwargs):
        super().__init__(runtime_config(cfg), clients, *args, **kwargs)
        self.reader_namespace = reader_namespace
        # Explicit reviewed relay signer; never infer it from response authors.
        self.claim_relay_pubkey = claim_relay_pubkey
        if reader_namespace is not None and reader_namespace != self.desk_app_id:
            raise failure('身份缓存属于其他读取应用', '先进行已验证的 union_id 状态迁移')
        if reader_namespace is None and any((self.state.idmap, self.state.emailmap, self.state.feishu_seen,
                                             self.state.f2r, self.state.members_synced)):
            raise failure('旧身份状态尚未迁移', '先验证 union_id 绑定并生成状态迁移候选，保留投递账本与回滚备份')

    def bot_admission_allowed(self, pubkey, app_id):
        predicate = getattr(self, 'bot_admission', None)
        try:
            return predicate is not None and predicate(pubkey, app_id) is True
        except Exception:
            return False  # Missing proof never delegates owner channel authority.

    def verify_identities(self):
        if not isinstance(self.clients.owner, BotLarkCli) or self.clients.owner is not self.clients.agents.get(self.desk_app_id):
            raise failure('读取客户端不是选定的同步 bot', '检查 hostd bot 客户端装配')
        if self.clients.buzz.whoami() != self.cfg['mirror_pubkey'] or gs._signer_pubkey(self.clients.mirror_key) != self.cfg['mirror_pubkey']:
            raise failure('镜像签名身份与绑定不一致', '检查本机镜像凭据')
        # Legacy idmap/emailmap have no app namespace. Rebuild projections within
        # each bot round instead of trusting data from the previous human app.
        self.owner_id = ''
        for pubkey, agent in self.cfg['agents'].items():
            client = self.clients.agents.get(agent['app_id'])
            if not isinstance(client, BotLarkCli) or client.identity() != (agent['app_id'], ''):
                raise failure('本机 bot profile 与应用不一致', '检查每个 agent 的本机应用凭据目录')
            scopes = client.bot_scopes()  # Actual bot-token API, not a user's auth status.
            if pubkey == self.cfg['desk_pubkey'] and any(not alternatives & scopes for alternatives in READ_SCOPE_GROUPS.values()):
                raise failure('同步 bot 缺少读取权限', '为应用开通群、成员、消息与表情读取权限后重试')
            self.verified_agents.add(pubkey)

    def load_people(self):
        super().load_people()
        signer = gs._signer_pubkey(self._signer())
        if self.roles.get(signer) not in ('owner', 'admin') or signer not in self.humans():
            raise failure('本机签名者不是已验证的频道 owner 或 admin', '检查频道授权与 people API 签名凭据')
        unions = [uid for uid, pk in self.id_to_pubkey.items() if pk == signer and gs.UNION_ID_RE.fullmatch(uid)]
        if len(unions) != 1:
            raise failure('频道 owner 缺少唯一的已验证 union_id', '完成 owner 的 union_id 绑定迁移，不要借用旧 owner_open_id')
        self.owner_id = unions[0]
        if self.reader_namespace is None:
            # Clear only unscoped identity projections, after the signer/people
            # preflight succeeds; no delivery IDs or reaction ACKs are removed.
            self.state.idmap.clear()
            self.state.emailmap.clear()

    def verify_desk(self):
        """Legacy method name only: any verified channel bot may be the sync bot."""
        sync = self.cfg['desk_pubkey']
        if self.roles.get(sync) != 'bot' or sync not in self.verified_agents:
            raise failure('同步 bot 不是已验证的频道成员', '将选定的 agent 作为 bot 加入频道并核对本机应用')
        listing = self.clients.owner.member_listing(self.cfg['chat_id'], 'union_id')
        if not listing.complete:
            raise failure('群成员列表不完整', '检查群安全限制与分页，获得完整名单后重试')
        if self.desk_app_id not in listing.bots:
            raise failure('同步 bot 尚未进入飞书群', '先将绑定选定的 bot 加入这个群')
        self.preverified_listing = listing
        self.bot_members = dict(listing.bots)

    def _claim_query(self, filters):
        """Whole signed packet through the shared bounded reader.

        Round executes synchronously on its worker thread. Async consumers must
        offload this boundary; never nest asyncio.run inside their active loop.
        The HTTP callable is the actual selected Clients seam; native transport
        remains an isolated child, while explicit offline IO is cooperative.
        """
        try:
            try: asyncio.get_running_loop()
            except RuntimeError: pass
            else: raise sync_app_claim.pending()
            pin = self.claim_relay_pubkey
            if not isinstance(pin,str) or not gs.HEX64_RE.fullmatch(pin): raise sync_app_claim.pending()
            key = gs.secret_hex(parse_env(read_owned(self.cfg['people_api']['signer_env_file']).decode()).get('BUZZ_PRIVATE_KEY',''),'owner')
            if gs._signer_pubkey(key) != gs._signer_pubkey(self._signer()): raise sync_app_claim.pending()
            relay = RecoveryRelay(self.clients.relay_url,key,pin,http=self.clients.http,
                                  now=self.auth_clock or (lambda:self.now))
            rows = asyncio.run(SignedReader(relay).read('query',filters=filters))
            if not isinstance(rows,list) or len(rows)>256: raise sync_app_claim.pending()
            for value in rows:
                if not any(value['kind'] in f['kinds'] and value['pubkey'] in f['authors']
                        and ('#d' not in f or any(value['tags'].count(['d',d])==1 for d in f['#d'])) for f in filters):
                    raise sync_app_claim.pending()
            return rows
        except Exception:
            raise sync_app_claim.pending() from None

    def _claim_attestation(self):
        try:
            return self._checked_claim_attestation()
        except Exception:
            raise sync_app_claim.pending() from None

    def _checked_claim_attestation(self):
        # Round setup can be cached by the worker. Announcements require fresh
        # local profile/scopes/people and actual signed authority, not cache.
        self.verify_identities()
        self.load_people()
        self.verify_desk()
        selected = self.cfg['agents'][self.cfg['desk_pubkey']]
        sync_app_claim.bot(self.clients.owner,self.desk_app_id,selected['lark_config_dir'],
                           selected['lark_data_dir'],self.cfg['chat_id'])
        owner = gs._signer_pubkey(self._signer())
        agent, mirror = self.cfg['desk_pubkey'], self.cfg['mirror_pubkey']
        profiles = self._claim_query([{'kinds':[0],'authors':sorted({agent,mirror}),'limit':257}])
        policies = self._claim_query([{'kinds':[30177],'authors':[owner],'#d':[agent],'limit':257}])
        sync_app_claim.profiles_and_app(profiles,policies,agent,mirror,owner,self.desk_app_id,self.now_ts)
        pin = self.claim_relay_pubkey
        if not isinstance(pin,str) or not gs.HEX64_RE.fullmatch(pin): raise sync_app_claim.pending()
        rows = self._claim_query([{'kinds':[39002],'authors':[pin],'#d':[self.cfg['channel_id']],'limit':257}])
        sync_app_claim.roster(rows,pin,self.cfg['channel_id'],agent,mirror,owner,self.now_ts)
        # Recheck the selected protected app after relay reads.
        if self.clients.owner.identity() != (self.desk_app_id,''): raise sync_app_claim.pending()
        return sync_app_claim.field(self.desk_app_id)

    def _keep_claim(self, view, me, own):
        app = self._claim_attestation()
        owner = gs._signer_pubkey(self._signer())
        entry = sync_app_claim.binding_entry(view.policies,self.cfg['mirror_pubkey'],owner,
                        self.cfg['channel_id'],gs.chat_ref(self.cfg['chat_id']),self.now_ts)
        # Missing/changed duty fields are repaired at once while retaining me's
        # original claimed_at. Legacy BindingClaim intentionally omits sync_app.
        if entry is None or not sync_app_claim.matches(entry.get('sync_app'),app['app_id']):
            own = None
        super()._keep_claim(view,me,own)

    def _write_claim(self, view, entry):
        if entry is not None:
            app = self._claim_attestation()
            entry = dict(entry,sync_app=app)
        # Exact withdrawal is still safe after a bot leaves. Parent preserves
        # all other policy fields/bindings and performs real owner signing.
        return super()._write_claim(view,entry)

    def _route_buzz_source(self, event):
        # These IDs are scheduling hints from the ordinary read, not notice
        # authority. The coordinator re-reads the entire signed/current scope.
        if (getattr(self, 'notice_hints_enabled', False) and event.get('kind') == 9
                and isinstance(event.get('id'), str) and gs.HEX64_RE.fullmatch(event['id'])
                and event.get('pubkey') in self.humans()
                and len(self.notice_ignored_source_ids) < 256):
            # The ordinary human path handles this ID. This removes only a
            # wake hint, never a durable notice/receipt or a foreign source.
            self.notice_ignored_source_ids.add(event['id'])
        if (getattr(self, 'notice_hints_enabled', False) and event.get('kind') == 9
                and self.roles.get(event.get('pubkey')) == 'bot'
                and event.get('pubkey') not in ({self.cfg['mirror_pubkey']} | self.other_mirrors)
                and isinstance(event.get('id'), str) and gs.HEX64_RE.fullmatch(event['id'])):
            # A persisted original needs scan/readback, not another observe
            # hint. This lets oversized ordinary windows progress in bounded
            # chunks without restarting from the same first256 forever.
            stored = self.mapping_store.get_delivery_notice(self.binding_id, event['id'])
            ids = self.notice_source_ids
            if stored is not None or self.mapping_store.conn.execute(
                    'SELECT 1 FROM notice_hint WHERE binding_id=? AND source_id=?',
                    (self.binding_id, event['id'])).fetchone():
                pass
            elif event['id'] in ids or len(ids) < 256:
                ids.add(event['id'])
            else:
                self.notice_hint_overflow = True
        if not getattr(self, 'own_outlets_active', False):
            return super()._route_buzz_source(event)  # Explicit ADR0026 M3 transition.
        # Agent bots remain exclusive to their own outlets. Explicit context
        # sources use the Desk's existing labelled-context route, not a human
        # identity or an agent grant.
        return event.get('pubkey') in self.humans() or self._context_buzz_source(event)

    def _context_buzz_source(self, event):
        """Read-only eligibility for a signed, explicitly enabled context root."""
        if gs.buzz_unmapped_sender_mode(self.cfg) != 'context' or not isinstance(event, dict):
            return False
        author, tags = event.get('pubkey'), event.get('tags')
        if (type(event.get('kind')) is not int or event['kind'] != 9
                or not isinstance(author, str) or not gs.HEX64_RE.fullmatch(author)
                or not isinstance(tags, list)
                or [t for t in tags if isinstance(t, list) and t[:1] == ['h']] != [['h', self.cfg['channel_id']]]
                or author in self.humans() or self.roles.get(author) == 'bot'
                or author in self.cfg['agents'] or author in self.directory
                or author in ({self.cfg['mirror_pubkey']} | self.other_mirrors)):
            return False
        store = getattr(self, 'mapping_store', None)
        if store is None or store.conn.execute('SELECT 1 FROM agent WHERE pubkey=?', (author,)).fetchone():
            return False
        return gs._nip01_event_verified(event)

    def _membership_plan(self, plan):
        # Preserve the observed neopace rule: Feishu controls human invitations
        # and removals; bot reconciliation and inbound membership remain active.
        if self.cfg.get('human_membership_sync') == 'feishu_to_buzz':
            plan = replace(plan, add_users=(), remove_users=())
        plan = super()._membership_plan(plan)
        occupied = getattr(self, '_opaque_bot_slots', 0)
        room = max(gs.MAX_BOTS_PER_CHAT - len(self.bot_members) + len(plan.remove_bots) - occupied, 0)
        return replace(plan, add_bots=plan.add_bots[:room],
                       blocked_bots=tuple(sorted({*plan.blocked_bots, *plan.add_bots[room:]})))

    def reconcile_members(self):
        if not self.owner_id:
            raise failure('owner 身份尚未验证', '先完成已签名的 union_id 人员映射')
        listing = self.clients.owner.member_listing(self.cfg['chat_id'], 'union_id')
        if not listing.complete:
            raise failure('群成员列表不完整，已停止成员变更', '检查分页与群安全限制，获得完整名单后重试')
        self.preverified_listing = listing
        self._opaque_bot_slots = len(getattr(listing, 'opaque_bots', ()))
        original = self.cfg['remove_extras']
        if not self.state.members_synced:
            self.cfg['remove_extras'] = False  # New/migrated identity space records its baseline without removals.
        try:
            super().reconcile_members()
            if (gs.membership_sync_mode(self.cfg) == 'buzz_to_feishu' and not self.state.members_synced
                    and not self.report['errors'] and not self.report['member_failures']):
                self.state.members_synced = self.now_ts
                self.persist()
        finally:
            self.cfg['remove_extras'] = original
            self._opaque_bot_slots = 0

    def recover_feishu_event(self, event, *, observed_message=None):
        """Directly discover roots missed by local ledger/hot-thread history.

        Returns the verified root snapshot for later delivery-ledger recovery.
        Registration repairs thread discovery; it does not invent a missing
        Feishu-to-Buzz delivery mapping or claim that the root was delivered.
        """
        root = self.clients.owner.root_for_event(self.cfg['chat_id'], event, observed_message=observed_message)
        if root is not None:
            root_id = root['message_id']
            self.state.threads[root_id] = self.now_ts
            self.state.polled.setdefault(root_id, self.state.floor)
            self.persist()
        return root

    def _approval_tags(self, inbound, reply_to):
        return None  # ADR-0028: messages/reactions never become invite authorizations.


@dataclass(frozen=True)
class UnionMigration:
    """In-memory reviewed candidate; caller owns backup and atomic persistence."""
    config: dict
    state: gs.State
    reader_app_id: str
    signer_pubkey: str
    signer_union_id: str
    reset_fields: tuple[str, ...]
    reaction_key_mapping: dict[str, str]


def prepare_union_migration(cfg, state, clients, *, authorized=False, now=None):
    """Read-only union preflight and state transformation, never apply implicitly.

    Preserve legacy owner fields in the candidate configuration for rollback.
    Every old reaction ACK is translated using its previous app's namespaced,
    recent people_seen proof followed by the live signed people mapping. Unknown
    or pending operations block migration; nothing is silently dropped.
    Caller must archive old config/state, then atomically persist the candidate
    and its reader_app_id namespace before enabling the bot worker.
    """
    if not authorized:
        raise failure('身份迁移尚未明确启用', '先审查 union_id 迁移候选与回滚备份，再明确启用迁移')
    if state.member_events or state.member_event_blocks:
        raise failure('还有待处理的成员事件，暂不能迁移', '先确认旧成员事件的结果，保留其原始签名与重试账本')
    when = now or datetime.now(timezone.utc)
    candidate_cfg = copy.deepcopy(dict(cfg))
    candidate_cfg['identity'] = 'union_id'
    candidate = copy.deepcopy(state)
    reader = candidate_cfg['agents'][candidate_cfg['desk_pubkey']]['app_id']
    report = gs._new_report()
    run = HostdRound(candidate_cfg, clients, candidate, report, when, lambda: None, reader_namespace=reader,
                     auth_clock=lambda: when)
    run.verify_identities()
    run.load_people()
    run.verify_desk()  # Full human + bot list and sync-bot presence; no member writes.
    union_by_pk = {pk: uid for uid, pk in run.id_to_pubkey.items()}
    old_app = cfg['owner_app_id']
    translated, reaction_key_mapping = {}, {}
    for key, value in state.f2r.items():
        if gs._is_pending(value) or gs._is_retry(value) or value == gs.UNKNOWN:
            raise failure('还有身份相关的待定表情投递，暂不能迁移', '先核实原投递结果并保留原重试事件')
        parts = key.split('|')
        if len(parts) != 3:
            raise failure('旧表情投递键无法验证', '检查旧账本格式，保留回滚备份')
        mid, operator, emoji = parts
        if gs.UNION_ID_RE.fullmatch(operator) and operator in run.id_to_pubkey:
            union = operator
        elif gs.OPEN_ID_RE.fullmatch(operator):
            proof = state.people_seen.get(f'o:{old_app}:{operator}', '')
            pk, _, timestamp = proof.partition('|')
            if (not gs.HEX64_RE.fullmatch(pk) or not timestamp.isdigit()
                    or not 0 <= run.now_ts - int(timestamp) <= gs.PEOPLE_CACHE_TTL or pk not in union_by_pk):
                raise failure('旧表情作者缺少可验证的跨应用映射', '先补齐旧应用人员证据与当前 union_id 绑定，不要猜测或删除 ACK')
            union = union_by_pk[pk]
        else:
            raise failure('旧表情作者无法在 union_id 空间中验证', '补齐作者绑定后重新生成迁移候选')
        newkey = f'{mid}|{union}|{emoji}'
        if newkey in translated:
            raise failure('表情迁移发生投递账本冲突', '核对重复身份与原投递事件，不要覆盖 ACK')
        translated[newkey] = value
        reaction_key_mapping[key] = newkey
    candidate.f2r = translated
    reset_fields = ('idmap', 'emailmap', 'feishu_seen', 'buzz_seen', 'member_notes', 'people_seen', 'members_synced')
    for field in reset_fields[:-1]:
        setattr(candidate, field, {})
    candidate.members_synced = 0
    # Only the current signed people response vouches for this new baseline cache.
    candidate.people_seen = {f'u:{uid}': f'{pk}|{run.now_ts}' for uid, pk in run.id_to_pubkey.items()}
    return UnionMigration(candidate_cfg, candidate, reader, gs._signer_pubkey(run._signer()), run.owner_id,
                          reset_fields, reaction_key_mapping)
