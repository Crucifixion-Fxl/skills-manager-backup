"""Run one binding's sync in phases, so an event only does the work it needs (ADR-0025).

Existing sync rules run through the bot-only HostdRound; this module decides which steps run and
*when*. Setup (identities, people, claim, directory) is kept for SETUP_TTL seconds and redone at once on a member
change or a reconnect; the claim is renewed before its lease runs out (the only clock-driven work, ADR-0022).
The round lock is shared with timers. SQLite is authoritative; legacy state is imported once and left intact.
"""
from __future__ import annotations

import os
import asyncio
import copy
import contextlib
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs  # noqa: E402
try:
    from . import bot_clients as bc, bot_round as br, delivery_mapping as dm, config as hc, outlet as ao
    from .safety import read_owned
    from .store import Store, BindingRecord
    from .state_store import StateAdapter
    from .recovery import plan_recovery
except ImportError:
    import bot_clients as bc
    import bot_round as br
    import delivery_mapping as dm
    import config as hc
    import outlet as ao
    from safety import read_owned
    from store import Store, BindingRecord
    from state_store import StateAdapter
    from recovery import plan_recovery

SETUP_TTL = 600          # people / directory / identities are re-read at least this often, and on any member change
CLAIM_RENEW = 25 * 60    # ADR-0022 lease is 30 min

PHASES = {
    "buzz": ("buzz_to_feishu", "buzz_reactions_to_feishu"),
    "feishu": ("feishu_to_buzz", "feishu_reactions_to_buzz"),
    "members": ("reconcile_members", "introduce_agents", "publish_membership_status"),
    "notice": (),  # Durable candidate discovery/receipts; never forwarding/cursor steps.
}


@dataclass
class Worker:
    config: Path
    state_dir: Path
    base_env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    setup_at: float = 0.0
    claim_at: float = 0.0
    verdict: str = ""
    last: dict[str, Any] = field(default_factory=dict)
    cached: dict[str, Any] = field(default_factory=dict)  # Round fields from the last full setup (who is who)
    store_path: Path | None = None
    binding_id: str = ""
    client_factory: Any = None
    migrate_bot_readers: bool = False
    clock: Any = None
    setup_fingerprint: str = ""
    retry_attempt: int = 0
    scheduler: Any = None
    request_priority: str = 'normal'
    outlet_specs: Any = None  # None: explicit M3 transition; even {} activates human-only routing.
    trusted_relays: Any = field(default_factory=frozenset)
    failure_formatter: Any = None  # Optional metadata-only observer; no transport dependency.
    latency_trace: Any = None
    http_pool: Any = None
    claim_relay_pubkey: str | None = None

    # Explicit own responsibility survives a missing/invalid local spec. Public
    # foreign roster entries never populate this set.
    outlet_responsibilities: set[str] = field(default_factory=set)
    notice_cursor: str = ""
    notice_clock: Any = None
    notice_sources: list[str] = field(default_factory=list)
    notice_deadline: int | None = None
    notice_retry_seconds: int = 5
    notice_hint_overflow: bool = False
    known_threads: set[str] = field(default_factory=set)
    outlet_cursor: str = ''  # Scheduling hints only; every effect still verifies current authority.
    _outlet_urgent_authors: dict[str, None] = field(default_factory=dict)
    _outlet_urgent_streak: int = 0
    outlet_sweep_pending: set[str] = field(default_factory=set)
    outlet_rescan: bool = True
    _outlet_readiness: dict = field(default_factory=dict)
    _readiness_public_scope: str = ''

    SETUP_FIELDS = ("roles", "names", "verified_agents", "directory", "introductions", "id_to_pubkey", "ambiguous_ids",
                    "bot_members", "owner_id", "emails", "other_mirrors", "people_cache", "agent_index")

    @contextlib.contextmanager
    def _storage(self, cfg):
        app = cfg['agents'][cfg['desk_pubkey']]
        name = self.binding_id or self.config.parent.name
        path = self.store_path or self.state_dir / 'hostd.sqlite3'
        with Store(path) as store:
            store.reconcile_bindings([BindingRecord(name, cfg['channel_id'], cfg['chat_id'], app['app_id'],
                str(self.config.absolute()), app['lark_config_dir'], app['lark_data_dir'], cfg['mirror_pubkey'])],
                now=int((self.clock() if self.clock else datetime.now(timezone.utc)).timestamp()))
            yield StateAdapter(store, name, self.state_dir.absolute(),
                profile_id=f"legacy:{cfg['owner_app_id']}:{gs.identity_mode(cfg)}")

    def _reader_state(self, cfg, clients, storage, state, now):
        app = cfg['agents'][cfg['desk_pubkey']]['app_id']
        meta = storage.metadata()
        if meta['reader_app_id'] == app:
            # The persisted verified epoch authorizes this runtime projection;
            # leave the legacy file intact for rollback.
            return state, app, dict(cfg, identity='union_id')
        if meta.get('imported_hash') and not self.migrate_bot_readers:
            raise bc.failure('旧状态仍属于原读取应用，尚未切换',
                '保存旧配置与状态备份，验证 bot 权限与 union_id 后使用 --migrate-bot-readers；回滚时恢复原 timer')
        migration = br.prepare_union_migration(cfg, state, clients, authorized=True, now=now)
        storage.migrate_identity(f'bot:{app}:union_id:v1', candidate=migration.state,
            reaction_key_mapping=tuple(migration.reaction_key_mapping.items()), now=int(now.timestamp()))
        self.cached.clear()
        return storage.load(), app, migration.config

    def run(self, dirty: set[str], *, force_setup: bool = False, threads: set[str] | None = None,
            notice_sources: tuple[str, ...] = (), outlet_authors: tuple[str, ...] = ()) -> dict[str, Any]:
        """Run the phases in `dirty` (subset of PHASES); setup first if stale. Returns the round report plus timings."""
        # Ownership transfers once at executor submission. Keep this metadata
        # even if setup fails; the loop must not replay an accepted snapshot.
        if type(outlet_authors) is tuple and len(outlet_authors) <= 256:
            for author in outlet_authors:
                if (type(author) is str and gs.HEX64_RE.fullmatch(author)
                        and author not in self._outlet_urgent_authors and len(self._outlet_urgent_authors) < 256):
                    self._outlet_urgent_authors[author] = None
        try:
            cfg = hc.load_config(self.config)
        except BaseException:
            self._outlet_readiness.clear()
            raise
        hint_overflow = self.notice_hint_overflow if dirty == {'notice'} else False
        for source in notice_sources:
            if (isinstance(source, str) and gs.HEX64_RE.fullmatch(source)
                    and source not in self.notice_sources and len(self.notice_sources) < 256):
                self.notice_sources.append(source)
            elif isinstance(source, str) and source not in self.notice_sources:
                hint_overflow = True
        self.notice_hint_overflow = hint_overflow
        try:
            now = self.clock() if self.clock else datetime.now(timezone.utc)
            lock = gs._lock(self.state_dir)
        except BaseException:
            self._outlet_readiness.clear()
            raise
        timings: dict[str, float] = {}
        try:
            if self.client_factory:
                clients = self.client_factory(cfg, self.base_env)
            elif self.http_pool is not None or self.trusted_relays:
                clients = bc.build_clients(cfg, self.base_env, http_pool=self.http_pool,
                                           scheduler=self.scheduler, trusted_relays=self.trusted_relays)
            else:
                clients = bc.build_clients(cfg, self.base_env)
            for client in clients.agents.values():
                if isinstance(client, bc.BotLarkCli):
                    client.scheduler, client.chat_id, client.priority = self.scheduler, cfg['chat_id'], self.request_priority
                    client.http_pool = self.http_pool
            with self._storage(cfg) as storage:
                state = storage.load()
                state, reader, cfg = self._reader_state(cfg, clients, storage, state, now)
                return self._run(cfg, clients, storage, state, reader, now, dirty, force_setup, threads, timings)
        except BaseException:
            self._outlet_readiness.clear()
            raise
        finally:
            lock.close()

    def _outlet_scope(self, pub, store):
        """Private local authority fingerprint; never persisted or sent as proof."""
        spec = self.outlet_specs[pub]
        config = read_owned(self.config)
        cfg = json.loads(config)
        paths = (spec.env_file, cfg['mirror_env_file'])
        identity = [str(getattr(spec, key, '')) for key in (
            'pubkey', 'owner_pubkey', 'app_id', 'status', 'env_file',
            'lark_config_dir', 'lark_data_dir', 'reader_config_dir', 'reader_data_dir', 'reader_app_id')]
        rows = []
        for sql, args in (
            ('SELECT pubkey,owner_pubkey,app_id,status FROM agent WHERE pubkey=?', (pub,)),
            ('SELECT agent_id,chat_id,chat_ref,binding_id,status FROM agent_chat WHERE agent_id=? AND chat_id=?', (pub,cfg['chat_id'])),
            ('SELECT channel_id,chat_id,sync_app_id,config_path,mirror_pubkey,chat_ref,claimed_at FROM binding WHERE binding_id=?', (self.binding_id,)),
            ('SELECT request_id,status,owner_pubkey,callback_app_id,card_generation FROM join_request WHERE agent_id=? AND binding_id=? ORDER BY request_id', (pub,self.binding_id)),
            ('SELECT d.* FROM join_decision d JOIN join_request r ON r.request_id=d.request_id WHERE r.agent_id=? AND r.binding_id=? ORDER BY d.request_id', (pub,self.binding_id)),
            ('SELECT p.* FROM effect_plan p JOIN join_request r ON r.request_id=p.request_id WHERE r.agent_id=? AND r.binding_id=? ORDER BY p.request_id', (pub,self.binding_id))):
            rows.append([tuple(row) for row in store.conn.execute(sql,args)])
        if store.conn.execute("SELECT 1 FROM delivery WHERE binding_id=? AND agent_id=? AND status IN ('pending','failed','unknown','waiting_receipt') LIMIT 1",(self.binding_id,pub)).fetchone():
            raise ValueError('outlet receipt is unsettled')
        value = [hashlib.sha256(config).hexdigest(), identity, rows,
                 [hashlib.sha256(read_owned(path)).hexdigest() for path in paths]]
        return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

    def outlet_readiness(self, pub, store):
        """Last real success only; unrelated rounds never renew its checked_at."""
        saved = self._outlet_readiness.get(pub)
        if saved is None:
            return {}
        try:
            if self.verdict not in ('won','off') or saved[0] != self._outlet_scope(pub,store):
                raise ValueError('outlet authority changed')
            return dict(saved[1]) if self._outlet_readiness.get(pub) is saved else {}
        except Exception:
            self._outlet_readiness.pop(pub,None)
            return {}

    def _failure_metadata(self, stage, error):
        if self.failure_formatter is not None:
            try:
                return self.failure_formatter(stage,error)
            except Exception:
                pass  # diagnostics cannot change original phase/outlet outcomes
        return None

    def _bot_admission(self, run, pub, app, *, outlet=False):
        """Authorize from protected local approval, never from outlet observations."""
        try:
            from .bot_admission import local_authorization, channel_mode, all_member_proof
        except ImportError:
            from bot_admission import local_authorization, channel_mode, all_member_proof
        spec = self.outlet_specs.get(pub) if isinstance(self.outlet_specs, dict) else None
        if (spec is None or getattr(spec, 'pubkey', None) != pub or getattr(spec, 'app_id', None) != app
                or getattr(spec, 'status', 'own_bot_verified') != 'own_bot_verified'
                or getattr(spec, 'reader_app_id', app) not in ('', app)
                or any(other != pub and getattr(candidate, 'app_id', None) == app
                       for other, candidate in self.outlet_specs.items())):
            return False
        options = dict(env_file=spec.env_file, owner=spec.owner_pubkey, trusted_relays=self.trusted_relays,
            config_dir=getattr(spec, 'lark_config_dir', None) or getattr(spec, 'reader_config_dir', None),
            data_dir=getattr(spec, 'lark_data_dir', None) or getattr(spec, 'reader_data_dir', None),
            config_path=self.config, outlet=outlet)
        cfg = local_authorization(run, pub, app, **options)
        if cfg is None:
            return False
        # Outlet itself performs its full public fresh proof using its own key.
        if outlet:
            return True
        if not self._bot_public_identity(run, cfg, pub, app, spec.owner_pubkey):
            return False
        import buzz_agent_join_requests as join
        mode = channel_mode(join.parse_env(read_owned(spec.env_file).decode()), cfg['channel_id'])
        if mode == 'all_member':
            try:
                now = self.clock() if self.clock else datetime.now(timezone.utc)
                all_member_proof(run, pub, spec.owner_pubkey,
                    lambda filters: gs._relay_query(cfg, run.clients.relay_url, filters,
                        run.clients.http, now, auth_clock=self.clock, complete_limit=257), int(now.timestamp()))
            except Exception:
                return False
        return (local_authorization(run, pub, app, **options) is not None
                and channel_mode(join.parse_env(read_owned(spec.env_file).decode()), cfg['channel_id']) == mode)

    def _bot_public_identity(self, run, cfg, pub, app, owner):
        import recovery_authority as authority
        now = self.clock() if self.clock else datetime.now(timezone.utc)
        rows = gs._relay_query(cfg, run.clients.relay_url,
            [{'kinds': [0], 'authors': [pub], 'limit': 2},
             {'kinds': [30177], 'authors': [owner], '#d': [pub], 'limit': 2}],
            run.clients.http, now, auth_clock=self.clock, complete_limit=4)
        profiles = [e for e in rows if e['kind'] == 0 and e['pubkey'] == pub]
        policies = [e for e in rows if e['kind'] == 30177 and e['pubkey'] == owner
                    and authority.tags(e, 'd') == [['d', pub]]]
        if (len(profiles) != 1 or len(policies) != 1 or len(rows) != 2
                or any(e['created_at'] > int(now.timestamp()) for e in rows)
                or authority.attested_owner(profiles[0], pub) != owner):
            return False
        body = authority.policy_body(policies[0], pub)
        return gs._claimed_app_id(json.dumps(body)) == app and not gs.declares_mirror(json.dumps(body))

    def _run_outlets(self, run, now, *, only=None):
        """Same round/SQL transaction domain; only actual local own-key adapters.

        Explicit records use pubkey/owner_pubkey/env_file/app_id, plus
        lark_config_dir/lark_data_dir (AgentRecord) or reader_config_dir /
        reader_data_dir (AgentSpec). Record.channels is a catalog snapshot;
        the current protected env allowlist is the channel authority.
        """
        specs = self.outlet_specs
        results = {}
        expected = (set(self.outlet_responsibilities) | set(run.cfg['agents'])) - {
            run.cfg['desk_pubkey'], run.cfg['mirror_pubkey']} - run.other_mirrors
        if not isinstance(specs, dict):
            return {'invalid_outlet_specs': {'status':'pending', 'reason':'invalid_spec_mapping',
                                           'pending':1, 'checked_at':int(now.timestamp())}}
        all_candidates = expected | set(specs)
        for pub in set(self._outlet_readiness) - all_candidates:
            self._outlet_readiness.pop(pub,None)
        self._outlet_urgent_authors = {pub: None for pub in self._outlet_urgent_authors
            if pub in all_candidates and pub in specs}
        if only is None:
            if self.outlet_rescan or not self.outlet_sweep_pending:
                self.outlet_sweep_pending = set(all_candidates)
                self.outlet_rescan = False
            self.outlet_sweep_pending.intersection_update(all_candidates)
            self.outlet_sweep_pending.update(self._outlet_urgent_authors)
            candidates = sorted(self.outlet_sweep_pending)
        else:
            candidates = sorted(all_candidates & set(only))
        following = [pub for pub in candidates if pub > self.outlet_cursor] or candidates
        urgent = [pub for pub in self._outlet_urgent_authors if pub in candidates]
        urgent_turn = only is None and bool(urgent) and self._outlet_urgent_streak < 3
        selected = urgent[0] if urgent_turn else (following[0] if following else None)
        if selected is not None:
            if only is None:
                if urgent_turn:
                    self._outlet_urgent_streak += 1
                else:
                    self.outlet_cursor = selected  # Only ordinary turns advance the fair sweep.
                    self._outlet_urgent_streak = 0
                self._outlet_urgent_authors.pop(selected, None)
            self._outlet_readiness.pop(selected,None)
            try:
                if self.latency_trace is not None:
                    self.latency_trace.record(self.binding_id or self.config.parent.name,
                        'outlet_selected', source=selected)
            except Exception:
                pass  # Metadata diagnostics cannot change selection or results.
        for pub in candidates:
            if only is not None and pub not in only:
                continue
            result = {'status':'pending', 'reason':'missing_spec', 'app_id':'',
                      'checked_at':int(now.timestamp()), 'scanned':0, 'acked':0, 'pending':1}
            results[pub] = result
            spec = specs.get(pub)
            adapter = None
            if spec is None: continue
            try:
                if (not isinstance(pub,str) or not gs.HEX64_RE.fullmatch(pub)
                        or getattr(spec,'pubkey',None)!=pub
                        or not gs.HEX64_RE.fullmatch(getattr(spec,'owner_pubkey',''))
                        or getattr(spec,'status','own_bot_verified')!='own_bot_verified'):
                    result['reason']='spec_identity_invalid';continue
                app = spec.app_id
                if not isinstance(app,str) or not gs.APP_ID_RE.fullmatch(app):
                    result['reason']='spec_app_invalid';continue
                result['app_id']=app
                if (any(other != pub and getattr(candidate,'app_id',None)==app
                        for other,candidate in specs.items())
                        or any(other != pub and candidate.get('app_id')==app
                               for other,candidate in run.cfg['agents'].items())):
                    result['reason']='binding_app_conflict';continue
                if pub != selected:
                    result['reason']='slice_yield';continue
                config_dir = getattr(spec,'lark_config_dir',None) or getattr(spec,'reader_config_dir',None)
                data_dir = getattr(spec,'lark_data_dir',None) or getattr(spec,'reader_data_dir',None)
                if not config_dir or not data_dir:
                    result['reason']='spec_profile_missing';continue
                config_dir, data_dir = Path(config_dir), Path(data_dir)
                if (not config_dir.is_absolute() or not data_dir.is_absolute()
                        or '..' in config_dir.parts or '..' in data_dir.parts
                        or getattr(spec,'reader_app_id',app) not in ('',app)):
                    result['reason']='spec_profile_invalid';continue
                import buzz_agent_join_requests as join
                try:
                    from .bot_admission import channel_mode
                except ImportError:
                    from bot_admission import channel_mode
                env = join.parse_env(read_owned(spec.env_file).decode())
                if (gs._signer_pubkey(gs.secret_hex(env.get('BUZZ_PRIVATE_KEY',''),'own agent'))!=pub
                        or env.get('BUZZ_ACP_AGENT_OWNER')!=spec.owner_pubkey
                        or channel_mode(env, run.cfg['channel_id']) is None):
                    result['reason']='current_env_authority_invalid';continue
                if run.roles.get(pub)!='bot':
                    result['reason']='current_channel_role_invalid';continue
                client = run.clients.agents.get(app)
                # Own app must be independently instantiated from its exact
                # pinned directories. Never substitute the sync client.
                if client is None:
                    client = bc.BotLarkCli(app,config_dir,data_dir,base_env=self.base_env,
                                           runner=run.clients.owner.runner,http_pool=self.http_pool,
                                           scheduler=self.scheduler,chat_id=run.cfg['chat_id'],
                                           priority=self.request_priority)
                    run.clients.agents[app]=client
                if (not isinstance(client,bc.BotLarkCli) or client.app_id!=app
                        or client.config_dir!=config_dir or client.data_dir!=data_dir
                        or client.identity()!=(app,'')):
                    result['reason']='actual_app_client_mismatch';continue
                configured = run.cfg['agents'].get(pub)
                if configured and (configured['app_id']!=app or Path(configured['lark_config_dir'])!=config_dir
                                   or Path(configured['lark_data_dir'])!=data_dir):
                    result['reason']='binding_app_mismatch';continue
                client.scheduler, client.chat_id, client.priority = self.scheduler, run.cfg['chat_id'], self.request_priority
                client.http_pool=self.http_pool
                client.active_phase='buzz'
                adapter = ao.AgentOutlet(run,pub,spec.env_file,bot_client=client,
                    trusted_relays=self.trusted_relays,http=run.clients.http,
                    clock=self.clock or (lambda: datetime.now(timezone.utc)),
                    initial_since=max(run.state.floor,run.state.buzz_floor,0))
                if adapter.owner!=spec.owner_pubkey:
                    result['reason']='attested_owner_mismatch';continue
                result['reason']='own_outlet_approval_pending'
                if not self._bot_admission(run, pub, app, outlet=True):
                    continue
                result['reason']='own_outlet_catchup_failed'
                if configured is None:
                    adapter.verify()
                    # Approved onboarding updates the protected local env,
                    # not the original timer config. Project only this round.
                    run.cfg['agents'][pub]={'app_id':app,'lark_config_dir':str(config_dir),
                                            'lark_data_dir':str(data_dir)}
                    run.verified_agents.add(pub)
                if not adapter.has_local_grant():
                    adapter.verify()
                    if not adapter.has_local_grant():
                        # Approved onboarding needs live outlet readiness before
                        # its final readback records the grant. Do not send or
                        # consume history/cursors while that proof is pending.
                        result.update(status='verified', reason='', pending=0)
                        self.outlet_sweep_pending.discard(pub)
                        continue
                result['scanned']=adapter.catch_up(max_events=1)
                rows = run.mapping_store.conn.execute("SELECT status,count(*) AS n FROM delivery WHERE binding_id=? AND agent_id=? GROUP BY status",
                    (run.binding_id,pub)).fetchall()
                statuses = {row['status']:row['n'] for row in rows}
                pending = sum(statuses.get(key,0) for key in ('pending','failed','unknown','waiting_receipt'))
                result['acked']=statuses.get('acked',0)
                result['pending']=pending
                result['status']='pending' if pending else ('acked' if result['acked'] else 'verified')
                result['reason']='receipt_unsettled' if pending else ''
                if adapter.slice_pending and not pending:
                    result.update(status='pending', reason='slice_yield', pending=1)
                if only is None and result['status'] != 'pending':
                    self.outlet_sweep_pending.discard(pub)
            except (Exception, SystemExit) as error:
                if isinstance(error,ao.OutletWaiting):
                    result['reason']='orphan_withdrawal_wait'
                    result['retry_at']=error.retry_at
                    continue  # Pending outlet, but no failed business operation.
                if isinstance(error,ao.OutletDeferred):
                    result['reason']='own_outlet_deferred'
                elif adapter is not None and getattr(adapter,'attempted_work',False):
                    result['reason']='own_outlet_attempt_failed'
                diagnostic=self._failure_metadata('own_outlet',error)
                if diagnostic is not None:
                    result['diagnostic']=diagnostic
                # Raw exceptions may carry keys/CLI arguments. Keep evidence in
                # the durable outlet ledger and fixed enum status only.
                continue
        for pub, result in results.items():
            if pub != selected and result.get('reason') == 'slice_yield':
                continue
            self._outlet_readiness.pop(pub,None)
            if result.get('status') in {'verified','acked'} and result.get('pending') == 0 and self.verdict in ('won','off'):
                try:
                    self._outlet_readiness[pub] = (self._outlet_scope(pub,run.mapping_store), dict(result))
                except Exception:
                    pass  # Missing local proof cannot create a readiness observation.
        return results

    async def _notice_pass(self, run, storage, observe_hints):
        from .delivery_notice import DeliveryNoticeCoordinator
        coordinator = DeliveryNoticeCoordinator(storage.store, storage.binding_id, run,
            utc_clock=self.notice_clock or (lambda: int((self.clock() if self.clock else
                datetime.now(timezone.utc)).timestamp())), monotonic_clock=time.monotonic)
        try:
            coordinator.scan_cursor = self.notice_cursor
            # A small slice yields the binding lock between expensive proofs.
            # Candidate rows are scheduling metadata, never authorization.
            for hint in storage.store.pending_notice_hints(storage.binding_id, limit=1) if observe_hints else ():
                source = hint['source_id']
                excluded = False
                try:
                    excluded = await coordinator.exclude_candidate(source)
                    if not excluded:
                        await coordinator.observe(source)
                except Exception:
                    pass  # One incomplete candidate must not starve durable receipts.
                finally:
                    storage.store.finish_notice_hint(storage.binding_id, source, excluded=excluded, now=int(
                        (self.clock() if self.clock else datetime.now(timezone.utc)).timestamp()))
            await coordinator.scan_once(limit=1)
        finally:
            # Joined close is inside this current Store/Round lifetime, also
            # when the caller is cancelled repeatedly or a read fails.
            try:
                await coordinator.close()
            finally:
                self.notice_cursor = coordinator.scan_cursor
                self.notice_deadline = coordinator.next_waiting_deadline
                self.notice_retry_seconds = coordinator.retry_after_seconds

    def _run_notices(self, run, storage, *, observe_hints):
        row = storage.store.conn.execute("SELECT status FROM binding WHERE binding_id=?",
                                         (storage.binding_id,)).fetchone()
        if row is None or row['status'] != 'active' or self.verdict != 'won':
            return 'binding_pending'  # The root alone records a successful ordinary round.
        try:
            asyncio.run(self._notice_pass(run, storage, observe_hints))
            return 'scanned'
        except Exception:
            # Fixed metadata only. Incomplete foreign source proofs do not
            # authorize notices or negate a valid local binding's own phases.
            return 'pending'

    def _run(self, cfg, clients, storage, state, reader, now, dirty, force_setup, threads, timings):
        active_phase = None
        checkpoint = {}
        cursor_fields = {'members': ('members_synced',), 'buzz': ('buzz_since', 'react_since'),
                         'feishu': ('feishu_since', 'polled')}
        def persist():
            candidate = state
            if active_phase is not None:
                candidate = copy.deepcopy(state)
                for field, value in checkpoint.items():
                    setattr(candidate, field, copy.deepcopy(value))
            # Ledger ACKs are durable immediately; cursors are committed only
            # after the entire phase proves its reads complete.
            storage.save(candidate, now=int(now.timestamp()))
        binding = f"{cfg['channel_id']}|{cfg['chat_id']}"
        self.known_threads = set(getattr(state, "threads", {}))
        if state.binding and state.binding != binding:
            raise gs.GroupSyncError("this state dir belongs to another channel or chat")
        report = gs._new_report()
        if gs.unmapped_sender_mode(cfg) == "context":
            report["context_to_buzz"] = 0
        run = dm.MappedHostdRound(cfg, clients, state, report, now, persist, reader_namespace=reader,
                       store=storage.store, binding_id=storage.binding_id,
                       claim_relay_pubkey=self.claim_relay_pubkey,
                       auth_clock=self.clock or (lambda: datetime.now(timezone.utc)))
        run.bot_admission = lambda pub, app: self._bot_admission(run, pub, app)
        run.latency_trace = self.latency_trace
        def trace(stage):
            if self.latency_trace is not None:
                self.latency_trace.record(storage.binding_id, stage)
        trace('worker_started')
        run.own_outlets_active = self.outlet_specs is not None
        run.notice_hints_enabled = bool(self.claim_relay_pubkey)
        run.notice_source_ids = set()
        run.notice_ignored_source_ids = set()
        run.notice_hint_overflow = self.notice_hint_overflow
        notice_only = dirty == {'notice'}
        outlet_results = {}
        t = time.monotonic()
        fingerprint = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
        fresh = (force_setup or "members" in dirty or notice_only or not self.cached
                 or fingerprint != self.setup_fingerprint
                 or time.time() - self.setup_at > SETUP_TTL)
        if fresh:
            # Who is who must be current: re-read on any member change, a reconnect, or after SETUP_TTL.
            run.verify_identities(); run.load_people()
            trace('people_ready')
            timings["setup_people"] = round(time.monotonic() - t, 2); t = time.monotonic()
        else:
            for name, value in self.cached.items():
                # Own outlets may extend this round's private config and
                # verified set. That projection is not part of setup authority
                # for the next round, which reloads the protected config.
                setattr(run, name, set(value) if name == 'verified_agents' else value)
        if fresh or time.time() - self.claim_at > CLAIM_RENEW or not self.verdict:
            self.verdict = run.check_claims(False); self.claim_at = time.time()
            trace('claim_ready')
            timings["claims"] = round(time.monotonic() - t, 2); t = time.monotonic()
        if self.verdict == "lost":
            self._outlet_readiness.clear()
            gs.prune_state(state); persist()
            report["hostd"] = {"verdict": "lost", "timings": timings}
            return report
        if fresh:
            run.load_directory(); run.verify_desk()
            trace('directory_ready')
            timings["directory_desk"] = round(time.monotonic() - t, 2)
            self.cached = {name: (set(run.verified_agents) if name == 'verified_agents'
                                 else getattr(run, name)) for name in self.SETUP_FIELDS}
            self.setup_at = time.time()
            self.setup_fingerprint = fingerprint
        public_scope = hashlib.sha256(json.dumps([fingerprint,run.roles,run.directory,
            sorted(run.verified_agents),sorted(run.other_mirrors),run.bot_members],sort_keys=True).encode()).hexdigest()
        if (public_scope != self._readiness_public_scope or self.verdict not in ('won','off')
                or getattr(run,'agent_lookup_failed',False)):
            self._outlet_readiness.clear()
        self._readiness_public_scope = public_scope
        if not state.binding and notice_only:
            raise bc.failure('通知恢复所需的绑定状态尚未建立',
                '先完成本机绑定的普通成员核验，再恢复原通知')
        if not state.binding:
            state.binding, state.floor = binding, int(now.timestamp()) - run._feishu_overlap()
            state.buzz_since = state.feishu_since = state.react_since = state.floor
        interrupted = set()
        cooperative = set()
        failure_diagnostics = []
        try:
            if 'feishu' in dirty and storage.store is not None:
                for target in storage.store.pending_targets(storage.binding_id):
                    try:
                        view = clients.owner.message_view(target['message_id'], 'open_id')
                        if view is None:
                            continue  # Unknown/deleted targets remain explicit pending evidence.
                        chat = view.get('chat_id')
                        if not isinstance(chat, str) or not gs.CHAT_ID_RE.fullmatch(chat):
                            raise bc.failure('事件目标的群无法核验', '检查消息读取结果与绑定')
                        if chat == cfg['chat_id']:
                            recovered = run.recover_feishu_event({'message_id': target['message_id'],
                                'root_id': target['root_id'], 'type': target['event_type']}, observed_message=view)
                            if recovered is None:
                                continue
                        # A direct same-app read either verified recovery or
                        # proved the target belongs to a different binding.
                        storage.store.ack_target(storage.binding_id, target['app_id'],
                            target['message_id'], target['event_type'])
                    except Exception:
                        report['errors'] += 1
            for phase in ("members", "buzz", "feishu"):
                if phase not in dirty or (phase == "members" and self.verdict == "unreadable"):
                    continue
                trace(phase + '_started')
                active_phase = phase
                checkpoint = {field: copy.deepcopy(getattr(state, field)) for field in cursor_fields[phase]}
                for client in clients.agents.values():
                    client.active_phase = phase
                completed = False
                try:
                    if phase == 'buzz' and self.outlet_specs is not None:
                        # Human replies can wait for an agent root. Recover its
                        # own-app ACK before that wait can abort this phase.
                        t = time.monotonic()
                        trace('outlets_started')
                        outlet_results = self._run_outlets(run,now)
                        trace('outlets_finished')
                        timings['own_outlets'] = round(time.monotonic() - t, 2)
                    for step in PHASES[phase]:
                        t = time.monotonic()
                        if step == "feishu_to_buzz":
                            run.feishu_to_buzz(only_threads=threads)
                        else:
                            getattr(run, step)()
                        timings[step] = round(time.monotonic() - t, 2)
                        if step == 'buzz_to_feishu' and outlet_results:
                            # One bounded dependency pass after human roots.
                            # Never retry an unsettled send in the same round.
                            retry = {pub for pub,item in outlet_results.items()
                                     if item['reason']=='own_outlet_catchup_failed'
                                     and not run.mapping_store.conn.execute(
                                         "SELECT 1 FROM delivery WHERE binding_id=? AND agent_id=? AND status IN ('pending','failed','unknown','waiting_receipt') LIMIT 1",
                                         (run.binding_id,pub)).fetchone()}
                            if retry:
                                outlet_results.update(self._run_outlets(run,now,only=retry))
                    if phase == 'feishu' and getattr(run, 'feishu_ingest_cooperative', False):
                        cooperative.add('feishu')
                    if phase == 'buzz' and len(set(self.notice_sources) | run.notice_source_ids) > 256:
                        run.notice_hint_overflow = True
                    if phase == 'buzz' and run.notice_hint_overflow:
                        # ID scheduling pressure holds the ordinary cursor, but
                        # does not invalidate complete local app/phase proofs.
                        interrupted.add('buzz')
                    if any(item['status']=='pending' for item in outlet_results.values()):
                        interrupted.add('buzz')
                        if any(item['status']=='pending' and item['reason'] not in ('slice_yield','orphan_withdrawal_wait') for item in outlet_results.values()):
                            report['errors'] += 1
                        if any(item['reason'] in ('slice_yield','orphan_withdrawal_wait') for item in outlet_results.values()):
                            cooperative.add('buzz')
                    completed = True
                except (gs.GroupSyncError, gs.CliError, OSError) as error:
                    diagnostic=self._failure_metadata('phase_'+phase,error)
                    if diagnostic is not None:
                        failure_diagnostics.append(diagnostic)
                    interrupted.add(phase)
                    report['errors'] += 1
                finally:
                    trace(phase + '_finished')
                    partial = not completed or phase in interrupted or any(phase in getattr(client, 'partial_reads', ()) for client in clients.agents.values())
                    if partial:
                        for field, value in checkpoint.items():
                            setattr(state, field, value)
                    if phase == 'buzz' and self.claim_relay_pubkey:
                        # Commit candidates before any phase cursor. Failure
                        # leaves the old cursor, so restart re-discovers them.
                        candidates = (set(self.notice_sources) | run.notice_source_ids) - run.notice_ignored_source_ids
                        if not storage.store.enqueue_notice_hints(storage.binding_id, candidates, now=int(now.timestamp())):
                            interrupted.add('buzz')
                            run.notice_hint_overflow = True
                            for field, value in checkpoint.items():
                                setattr(state, field, value)
                        else:
                            self.notice_sources.clear()
                    active_phase = None
                    persist()
        finally:
            self.known_threads = set(getattr(state, "threads", {}))
            gs.prune_state(state); persist()
        if self.claim_relay_pubkey and self.notice_sources:
            # Feed hints can accompany a Feishu-only round. No replay cursor
            # relies on them, but they must survive worker/process lifetimes.
            if storage.store.enqueue_notice_hints(storage.binding_id, self.notice_sources, now=int(now.timestamp())):
                self.notice_sources.clear()
            else:
                run.notice_hint_overflow = True
        self.notice_hint_overflow = run.notice_hint_overflow
        t = time.monotonic()
        notice_state = (self._run_notices(run, storage, observe_hints=True) if notice_only else 'pending') if self.claim_relay_pubkey else 'unconfigured'
        timings['delivery_notices'] = round(time.monotonic() - t, 2)
        pending_targets = len(storage.store.pending_targets(storage.binding_id)) if storage.store is not None else 0
        ordinary_interrupted = interrupted - ({'buzz'} if run.notice_hint_overflow and not report['errors'] else set())
        partial_reads = (ordinary_interrupted - cooperative) | set().union(*(getattr(client, 'partial_reads', set()) for client in clients.agents.values()))
        recovery = plan_recovery(state, now=int(now.timestamp()), partial_reads=partial_reads,
                                 pending_targets=bool(pending_targets), attempt=self.retry_attempt,
                                 feishu_ingest_retry_at=getattr(run, 'feishu_ingest_retry_at', None))
        if not notice_only:
            self.retry_attempt = self.retry_attempt + 1 if recovery.phases else 0
        if partial_reads and not report['errors']:
            report['errors'] = 1
        outlet_pending = sum(item['pending'] for item in outlet_results.values() if item['status']=='pending')
        cooperative_retry=int(now.timestamp())+1
        waits=[item for item in outlet_results.values() if item['status']=='pending']
        if (cooperative == {'buzz'} and not report['errors'] and not recovery.phases and waits
                and all(item['reason']=='orphan_withdrawal_wait' for item in waits)):
            cooperative_retry=min(item['retry_at'] for item in waits)
        report["hostd"] = {"verdict": self.verdict, "dirty": sorted(dirty), "timings": timings,
                           "setup": "fresh" if fresh else "cached", "threads": None if threads is None else len(threads),
                           "pending_targets": pending_targets, 'retry_phases': [] if notice_only else sorted(set(recovery.phases) | cooperative),
                           'cooperative_phases': sorted(cooperative),
                           'next_retry_at': None if notice_only else (cooperative_retry if cooperative else recovery.next_retry_at),
                           'recovery_reasons': [] if notice_only else list(recovery.reasons),
                           'outlet_pending':outlet_pending, 'outlet_results':outlet_results,
                           'notice': {'status': notice_state, 'pending_hints':len(self.notice_sources) + (len(storage.store.pending_notice_hints(storage.binding_id)) if self.claim_relay_pubkey else 0),
                                      'backlog':run.notice_hint_overflow,
                                      'deadline':self.notice_deadline, 'retry_seconds':self.notice_retry_seconds}}
        if hasattr(run, 'feishu_ingest_status'):
            report['hostd']['feishu_ingest'] = run.feishu_ingest_status
        report['hostd']['failure_diagnostics'] = (failure_diagnostics +
            [item['diagnostic'] for item in outlet_results.values() if 'diagnostic' in item])[:8]
        self.last = report
        return report
