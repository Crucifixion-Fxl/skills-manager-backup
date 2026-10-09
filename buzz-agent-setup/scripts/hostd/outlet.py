"""ADR0026: one own-agent, own-channel outlet using its local bot and key.

The mapping round is the actual hostd adapter, not an observer. It resolves
public receipts and checkpoints IDs; this outlet performs its own bot writes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import secrets
import time
from urllib.parse import quote

try:
    from .bot_clients import BotLarkCli, failure, _trusted_relay
    from .delivery_mapping import DeliveryMapping, message_card, _one
    from .safety import read_owned
    from .async_io import thread_call
    from . import reaction_inbox, outlet_pending, outlet_work
    from .relay_history import signed_history
except ImportError:
    from bot_clients import BotLarkCli, failure, _trusted_relay
    from delivery_mapping import DeliveryMapping, message_card, _one
    from safety import read_owned
    from async_io import thread_call
    import reaction_inbox
    import outlet_pending
    import outlet_work
    from relay_history import signed_history
import buzz_feishu_group_sync as gs
import recovery_authority as authority

KINDS = [9, 7, 5, 40003]
QUERY_LIMIT = 1500


class OutletDeferred(gs.GroupSyncError):
    """Unresolved work has a durable retry lease; no same-round full rescan."""
    def __init__(self):
        super().__init__(str(failure('部分 agent 消息等待原话题或实际回执，其他独立话题继续',
            '等待有界重试；若持续未恢复，核对原话题与投递账本，不要重新发送结果未知的操作')))


class OutletWaiting(OutletDeferred):
    """Only an unpaired retained withdrawal remains; no business effect failed."""
    def __init__(self, retry_at):
        super().__init__()
        self.retry_at=retry_at


class AgentOutlet:
    def __init__(self, mapping_round, agent_pubkey, agent_env_file, *, bot_client,
                 trusted_relays=(), http=None, clock=None, initial_since=0):
        self.round = mapping_round
        self.store, self.binding_id = mapping_round.mapping_store, mapping_round.binding_id
        self.agent, self.client = agent_pubkey, bot_client
        self.env_file, self.trusted_relays = agent_env_file, trusted_relays
        self.channel, self.chat = mapping_round.cfg['channel_id'], mapping_round.cfg['chat_id']
        self.clock = clock or (lambda: gs.datetime.now(gs.timezone.utc))
        self.http = http or mapping_round.clients.http
        if type(initial_since) is not int or not 0 <= initial_since < 2 ** 63:
            raise failure('agent 发送器初始回放边界无效', '使用已核实的绑定历史边界或固定激活时间，不能借用心跳时间')
        self.initial_since = initial_since
        self._verification_count = 0
        try:
            if not isinstance(agent_pubkey, str) or not gs.HEX64_RE.fullmatch(agent_pubkey) or not isinstance(bot_client, BotLarkCli):
                raise ValueError
            env = {}
            for line in read_owned(agent_env_file).decode().splitlines():
                name, sep, value = line.partition('=')
                if sep and name in gs.BUZZ_ENV_KEYS:
                    env[name] = value.strip().strip('"').strip("'")
            self.key = gs.secret_hex(env.get('BUZZ_PRIVATE_KEY', ''), 'outlet key')
            self.relay = _trusted_relay(env.get('BUZZ_RELAY_URL', ''), trusted_relays)
            self.auth = json.loads(env.get('BUZZ_AUTH_TAG', 'null'))
            if (gs._signer_pubkey(self.key) != agent_pubkey or self.relay != mapping_round.clients.relay_url
                    or not isinstance(self.auth, list) or len(self.auth) != 4 or self.auth[0] != 'auth'
                    or not all(isinstance(v, str) for v in self.auth)):
                raise ValueError
            self.owner = authority.attested_owner({'tags': [self.auth]}, agent_pubkey)
            if self.auth[2]:
                raise ValueError  # Conditional credentials need operation-specific evaluation; never bypass it.
            self.app = bot_client.identity()[0]
        except Exception:
            raise failure('agent 发送器凭据或应用身份无法验证', '核对这个 agent 自己的本机密钥与 bot profile；不要借用同步身份') from None

    def _query(self, filters, *, require_below_limit=False):
        url = gs.relay_query_url(self.relay)
        body = json.dumps(filters, separators=(',', ':')).encode()
        headers = {'Authorization': gs.nip98_header(self.key, 'POST', url, self.clock(), body=body),
                   'Content-Type': 'application/json', 'Accept': 'application/json',
                   'x-auth-tag': json.dumps(self.auth, separators=(',', ':'))}
        try:
            status, raw = self.http(url, headers, gs.DIRECTORY_TIMEOUT, body=body)
            events = json.loads(raw) if status == 200 else None
            if (not isinstance(events, list) or len(events) > QUERY_LIMIT
                    or (require_below_limit and len(events) >= QUERY_LIMIT)
                    or any(not gs._nip01_event_verified(ev) for ev in events)):
                raise ValueError
            return events
        except Exception:
            raise failure('agent 发送器没有读到完整可信的 relay 应答', '检查自己的 relay 权限、签名与网络；不能将失败当成没有旧消息') from None

    def _local_authorized(self, *, legacy_only=False):
        try:
            from .bot_admission import local_authorization
        except ImportError:
            from bot_admission import local_authorization
        return local_authorization(self.round, self.agent, self.app, env_file=self.env_file,
            owner=self.owner, config_dir=self.client.config_dir, data_dir=self.client.data_dir,
            trusted_relays=self.trusted_relays, outlet=True, legacy_only=legacy_only) is not None

    def has_local_grant(self):
        return self.store.conn.execute("""SELECT 1 FROM agent_chat ac JOIN agent a ON a.pubkey=ac.agent_id
            WHERE ac.agent_id=? AND ac.chat_id=? AND ac.chat_ref=? AND ac.binding_id=?
            AND ac.status='active' AND a.status='active' AND a.owner_pubkey=? AND a.app_id=?""",
            (self.agent, self.chat, gs.chat_ref(self.chat), self.binding_id, self.owner, self.app)).fetchone() is not None

    def verify(self):
        """Fresh signed directory, winning claim, bot identity and full membership."""
        now = int(self.clock().timestamp())
        try:
            if not self._local_authorized():
                raise ValueError
            import buzz_agent_join_requests as join
            try:
                from .bot_admission import channel_mode, all_member_proof
            except ImportError:
                from bot_admission import channel_mode, all_member_proof
            mode = channel_mode(join.parse_env(read_owned(self.env_file).decode()), self.channel)
            if mode == 'all_member':
                all_member_proof(self.round, self.agent, self.owner, self._query, now)
            # One fresh request for independent public reads. The relay
            # appends each filter's results; this is not an atomic snapshot.
            # Explicit per-filter caps, checked before selecting owners or
            # policies, keep a truncated page from proving no rival/revocation.
            snapshot = self._query([{'kinds': [0], 'authors': [self.agent], 'limit': 257},
                                    {'kinds': [30177], 'limit': 1000}])
            profile_events = [ev for ev in snapshot if ev['kind'] == 0 and ev['pubkey'] == self.agent]
            policies = [ev for ev in snapshot if ev['kind'] == 30177]
            if (len(profile_events) >= 257 or len(policies) >= 1000
                    or len(profile_events) + len(policies) != len(snapshot)):
                raise ValueError
            prof = authority.latest([ev for ev in profile_events if ev['kind'] == 0 and ev['pubkey'] == self.agent])
            if prof is None or authority.attested_owner(prof, self.agent) != self.owner:
                raise ValueError
            policy = authority.latest([ev for ev in policies if ev['kind'] == 30177 and ev['pubkey'] == self.owner
                                       and _one(ev['tags'], 'd') == self.agent])
            body = authority.policy_body(policy, self.agent) if policy else {}
            if gs._claimed_app_id(json.dumps(body)) != self.app or gs.declares_mirror(json.dumps(body)):
                raise ValueError
            if self.round.roles.get(self.agent) != 'bot' or self.round.roles.get(self.round.cfg['mirror_pubkey']) != 'bot':
                raise ValueError
            def claim_query(filters):
                return policies if filters == [{'kinds': [30177]}] else self._query(filters)
            view = gs.read_binding_claims(claim_query, also=[self.round.cfg['mirror_pubkey']],
                scope=(self.channel, gs.chat_ref(self.chat)))
            mirrors = {c.mirror for c in view.claims if c.channel == self.channel or c.chat_ref == gs.chat_ref(self.chat)}
            profiles = self._query([{'kinds': [0], 'authors': sorted(mirrors)}]) if mirrors else []
            verified = {}
            for mirror in mirrors:
                latest = authority.latest([ev for ev in profiles if ev['kind'] == 0 and ev['pubkey'] == mirror])
                if latest:
                    verified[mirror] = authority.attested_owner(latest, mirror)
            claims = [c for c in view.claims if gs.claim_valid(c, now)
                      and verified.get(c.mirror) == c.owner
                      and ((c.channel == self.channel and self.round.roles.get(c.mirror) == 'bot'
                            and self.round.roles.get(c.owner) in ('owner', 'admin'))
                           or (c.channel != self.channel and c.chat_ref == gs.chat_ref(self.chat)))]
            mine = [c for c in claims if (c.mirror, c.channel, c.chat_ref) ==
                    (self.round.cfg['mirror_pubkey'], self.channel, gs.chat_ref(self.chat))]
            if len(mine) != 1 or any(gs.claim_beats(c, mine[0]) for c in claims if c != mine[0]):
                raise ValueError
            if self.client.identity()[0] != self.app:
                raise ValueError
            self.client.bot_scopes()
            listing = self.client.member_listing(self.chat, 'union_id')
            if not listing.complete or self.app not in listing.bots:
                raise ValueError
            with self.store.transaction():
                if (not self._local_authorized()
                        or channel_mode(join.parse_env(read_owned(self.env_file).decode()), self.channel) != mode):
                    raise ValueError
                self.store.register_agent(self.agent, owner_pubkey=self.owner, app_id=self.app, now=now)
                # Materialize explicit protected legacy authority only when
                # missing. Never reactivate a negative grant or infer a dynamic
                # grant from public membership/readiness. No network in this tx.
                if self._local_authorized(legacy_only=True):
                    self.store.conn.execute('INSERT OR IGNORE INTO agent_chat VALUES(?,?,?,?,?,?)',
                        (self.agent, self.chat, gs.chat_ref(self.chat), self.binding_id, 'active', now))
            self._verification_count += 1
            return listing
        except Exception:
            raise failure('agent 自己的发送权限、目录或群绑定尚未确认', '核对已签名的应用声明、有效绑定 claim 与完整群成员；保留原消息等待重试') from None

    def _channel_matches(self, event):
        tags = event.get('tags') or []
        if _one(tags, 'h') == self.channel:
            return True
        # Stock CLI reactions/withdrawals omit h. Prove the reaction's signed
        # original target, or our own saved withdrawal receipt, in this binding.
        if event.get('kind') not in (5, 7) or any(t[:1] == ['h'] for t in tags):
            return False
        refs = [t for t in tags if t[:1] == ['e']]
        if len(refs) != 1 or len(refs[0]) < 2 or not gs.HEX64_RE.fullmatch(refs[0][1]):
            return False
        target = refs[0][1]
        if event['kind'] == 7:
            return self._original(target) is not None
        old = self.store.delivery_by_source(self.binding_id, target, 'r2f', agent_id=self.agent)
        receipt = self.store.outlet_receipt(old['id']) if old else None
        return bool(receipt and receipt['sender_app_id'] == self.app)

    def _valid(self, event):
        return self._valid_envelope(event) and self._channel_matches(event)

    def _valid_envelope(self, event):
        try:
            tags = event.get('tags')
            return (isinstance(event, dict) and type(event.get('kind')) is int and event['kind'] in KINDS
                    and event.get('pubkey') == self.agent
                    and isinstance(tags, list) and len(tags) <= 4096
                    and all(isinstance(t, list) and t and len(t) <= 64 and all(isinstance(v, str) and len(v) <= 8192 for v in t) for t in tags)
                    and type(event.get('created_at')) is int and 0 <= event['created_at'] < 2 ** 63
                    and isinstance(event.get('content'), str) and len(event['content'].encode()) <= 2 ** 20
                    and gs._nip01_event_verified(event))
        except Exception:
            return False

    def _original(self, event_id):
        events = self._query([{'kinds': [9], '#h': [self.channel], 'ids': [event_id], 'limit': 2}])
        matches = [ev for ev in events if ev['id'] == event_id and ev['kind'] == 9
                   and _one(ev['tags'], 'h') == self.channel]
        return matches[0] if len(matches) == 1 else None

    def _latest_edit(self, event, original_id):
        edits = self._query([{'kinds': [40003], 'authors': [self.agent], '#h': [self.channel],
                              '#e': [original_id], 'limit': QUERY_LIMIT}], require_below_limit=True)
        if any(not self._valid(edit) or edit['kind'] != 40003 or gs.edit_target(edit) != original_id for edit in edits):
            raise failure('编辑历史没有返回完整且一致的原作者证明', '按实际作者、唯一频道和原消息重新读取；不覆盖已投递的新内容')
        # Include this trusted input: it may arrive over WSS just before the
        # relay query's read snapshot. Event ID breaks same-second ties.
        return max([event, *edits], key=lambda edit: (edit['created_at'], edit['id']))

    def _target(self, event):
        known = gs.feishu_id_for_buzz(self.round.state, event['id'])
        if known:
            proof = self.round.resolve_feishu(known)
            if proof is None or proof.event_id != event['id']:
                raise failure('已有投递记录无法通过实际原消息验证', '核对原消息链接、真实作者与发送应用；不能把旧代发视为自己的投递')
            return proof.message_id
        mapping = self.round.resolve_buzz(event)
        return mapping.message_id if mapping else None

    def _mentions(self, event, listing):
        apps = self.round._directory_apps()
        seen, mentions = {event['pubkey']}, []
        for tag in event['tags']:
            if tag[:1] != ['p'] or len(tag) < 2 or tag[1] in seen or not gs.HEX64_RE.fullmatch(tag[1]):
                continue
            pk = tag[1]
            seen.add(pk)
            # Fresh bot member IDs belong to this outlet's own app. Human email
            # comes only from signed people mapping; never reuse sync-app IDs.
            mentions.append(gs.CardMention(self.round.names.get(pk) or pk[:12], self.round.emails.get(pk),
                                           listing.bots.get(apps.get(pk))))
        return tuple(mentions)

    def deliver(self, event):
        return self._deliver(event, frozenset())

    def _dependency_target(self, original, ancestors):
        target = self._target(original)  # An invalid known mapping must raise.
        if target is None and original.get('kind') == 9 and self._valid(original):
            # Only this author's exact signed parent can be materialized by
            # this bot. Other authors still require their real mapped receipt.
            return self._deliver(original, ancestors, dependency=True)
        return target

    def _deliver(self, event, ancestors, *, dependency=False, retained=False):
        if not self._valid(event):
            raise failure('事件不属于这个 agent 的唯一频道订阅', '核对原作者签名和唯一频道；不要代发其他作者或频道')
        if event['id'] in ancestors or len(ancestors) >= 8:
            raise failure('消息依赖链无法完整核实', '检查原话题的有界、无环依赖；不猜测父消息或另建话题')
        ancestors = ancestors | {event['id']}
        direction = 'e2f' if event['kind'] == 40003 else 'b2f' if event['kind'] == 9 else 'r2f'
        if not dependency and not retained and event['created_at'] < self.initial_since and self.store.delivery_by_source(
                self.binding_id, event['id'], direction, agent_id=self.agent) is None:
            return None  # Older roots can still resolve directly for a new reply.
        listing = self.verify()
        if not self.has_local_grant():
            raise failure('agent 接入授权尚未完成', '保留原消息，等待已批准接入的运行核验完成')
        self.round.now = self.clock()
        now = int(self.clock().timestamp())
        kind = event['kind']
        if kind in (7, 5):
            return self._reaction(event, now, ancestors)
        original, parent, target = event, None, None
        if kind == 40003:
            eid = gs.edit_target(event)
            original = self._original(eid) if eid else None
            if original is None or original['pubkey'] != self.agent:
                raise failure('编辑的原消息或作者无法验证', '读取实际原消息，不能编辑别人的投递')
            target = self._target(original)
            if target is None:
                # Do not create old roots for superseded or incomplete edits.
                if self._latest_edit(event, original['id'])['id'] != event['id']:
                    return None
                target = self._dependency_target(original, ancestors)
            if target is None:
                raise failure('编辑目标尚未完成实际投递', '先恢复原消息的公开对应关系，再重试编辑')
        else:
            target = self._target(event)
            root = gs.buzz_thread_root(event) or gs._buzz_parent(event)
            if root:
                root_event = self._original(root)
                parent = self._dependency_target(root_event, ancestors) if root_event else None
                if parent is None:
                    raise failure('回复的真实根投递尚未确认', '恢复原话题的消息对应关系；回复会等待')
                actual = self.client.root_for_event(self.chat, {'message_id': parent})
                if actual is None:
                    raise failure('自己的 bot 无法读取实际回复根', '检查本应用对这个群与根消息的权限')
                parent = actual['message_id']
        direction = 'e2f' if kind == 40003 else 'b2f'
        prior = self.store.delivery_by_source(self.binding_id,event['id'],direction,agent_id=self.agent)
        card = message_card(original, self.round.names.get(self.agent) or self.agent[:12], event['content'],
                            self.round.cfg['people_api']['base_url'], self.channel,
                            mentions=self._mentions(original, listing), compact=True)
        if prior is not None and prior['content_hash'] != hashlib.sha256(card.encode()).hexdigest():
            # Preserve the exact pre-upgrade payload for an existing operation,
            # including UNKNOWN recovery. reserve_delivery still rejects any
            # mismatch against both formats; this is not a new send key.
            for previous_format in ('preview_v5', 'preview_v4', 'preview_v3', 'preview_v2', 'preview_v1', False):
                card = message_card(original, self.round.names.get(self.agent) or self.agent[:12], event['content'],
                                    self.round.cfg['people_api']['base_url'], self.channel,
                                    mentions=self._mentions(original, listing), compact=previous_format)
                if prior['content_hash'] == hashlib.sha256(card.encode()).hexdigest():break
        delivery = self.store.reserve_delivery(self.binding_id, event['id'], direction, agent_id=self.agent,
            source_at=event['created_at'], content_hash=hashlib.sha256(card.encode()).hexdigest(), root_id=parent or '', now=now)
        images = self._prepare_images(event, parent, now) if kind == 9 else None
        if delivery.status == 'acked':
            if images:
                self._send_images(images, event, now, advance_cursor=not dependency)
            return delivery.target_id
        if kind == 40003:
            if delivery.status == 'skipped':
                return None
            latest = self._latest_edit(event, original['id'])
            if latest['id'] != event['id']:
                self.store.settle_delivery(delivery.id, outcome='skipped', now=now)
                return None  # Superseded is non-delivery, never a fabricated ACK.
        record = self.store.delivery_record(delivery.id)
        if not (kind == 9 and target) and now - record['created_at'] > gs.FEISHU_RETRY_WINDOW_SECONDS:
            raise failure('这次投递已超过飞书幂等重试窗口', '先核实实际消息与回执，不能重新发送未知结果')
        if delivery.status == 'failed':
            self.store.retry_delivery(delivery.id, now=now)
        if delivery.status not in ('pending', 'failed'):
            raise failure('这次投递结果仍需人工核实', '恢复实际回执后再处理，不要创建重复消息')
        if kind == 9 and target:
            mid = target  # Recovered public receipt: no additional send.
        else:
            key = 'ho-' + hashlib.sha256((self.channel + event['id'] + direction).encode()).hexdigest()[:40]
            try:
                trace = getattr(self.round, 'latency_trace', None)
                if trace is not None: trace.record(self.binding_id, 'outlet_send_started', source=event['id'])
                if kind == 40003:
                    self.client.update_card(target, card)
                    mid = target
                else:
                    mid = self.client.reply_card(parent, card, key) if parent else self.client.send_card(self.chat, card, key)
                if trace is not None: trace.record(self.binding_id, 'outlet_send_finished', source=event['id'], target=mid)
            except gs.CliError as exc:
                if exc.definite:
                    self.store.fail_delivery(delivery.id, now=now)
                raise failure('agent 自己的 bot 投递尚未得到确认', '在原幂等窗口内核实并重试同一请求，不要借用其他 bot') from None
            finally:
                # A lost response may have created a readable receipt. A history
                # snapshot from before this write cannot prove its absence.
                self.round._history.clear()
        if kind == 9:
            self.round._adopt(DeliveryMapping(event['id'], mid, parent or mid, gs.buzz_thread_root(event) or gs._buzz_parent(event), 'b2f', self.app))
            self.round.state.images[event['id'] + ':thread'] = parent or '-'
        self.store.ack_outlet_delivery(delivery.id, mid, self.app, now=now, advance_cursor=not dependency)
        if images:
            self._send_images(images, event, now, advance_cursor=not dependency)
        return mid

    def _prepare_images(self, event, parent, now):
        refs, over, _ = gs.event_images(event)
        if not refs and not over:
            return None
        for index, _ in enumerate(refs):
            self.store.reserve_delivery(self.binding_id, f"{event['id']}:{index}", 'image', agent_id=self.agent,
                                        source_at=event['created_at'], root_id=parent or '', now=now)
        return gs.Outbound(event['id'], self.app, event['content'], gs._buzz_parent(event), images=refs, images_over=over)

    def _send_images(self, out, event, now, *, advance_cursor=True):
        self.round._images_to_feishu(out, event['created_at'])
        for index, _ in enumerate(out.images):
            source = f"{event['id']}:{index}"
            delivery = self.store.reserve_delivery(self.binding_id, source, 'image', agent_id=self.agent,
                source_at=event['created_at'], now=now)
            value = self.round.state.images.get(source)
            if value == gs.SKIPPED:
                self.store.settle_delivery(delivery.id, outcome='skipped', now=now)
            elif value == gs.FAILED:
                self.store.settle_delivery(delivery.id, outcome='abandoned', now=now)
            elif value == gs.UNKNOWN:
                self.store.fail_delivery(delivery.id, unknown=True, now=now)
            elif isinstance(value, str) and gs.MESSAGE_ID_RE.fullmatch(value):
                self.store.ack_delivery(delivery.id, value, now=now, advance_cursor=advance_cursor)
        self.round.persist()

    def _reaction(self, event, now, ancestors=frozenset()):
        deleting = event['kind'] == 5
        refs = [t[1] for t in event['tags'] if t[:1] == ['e'] and len(t) >= 2]
        if len(refs) != 1 or not gs.HEX64_RE.fullmatch(refs[0]):
            raise failure('表情操作没有唯一可验证的目标', '每个撤回操作指向一条自己的已投递表情，保留歧义事件等待核实')
        old, receipt = None, None
        if deleting:
            old = self.store.delivery_by_source(self.binding_id, refs[0], 'r2f', agent_id=self.agent)
            receipt = self.store.outlet_receipt(old['id']) if old else None
            if not receipt or receipt['sender_app_id'] != self.app:
                raise failure('没有自己的已确认表情回执', '先恢复原表情的实际 bot 回执，不能撤回其他人的表情')
            target, emoji, rid = receipt['target_message_id'], receipt['emoji'], receipt['reaction_id']
        else:
            emoji = gs.merged_reaction_map(self.round.cfg.get('reaction_map') or {}).get(gs._reaction_emoji(event['content']))
            if emoji not in {'GLANCE', 'Typing', 'DONE', 'THUMBSUP', 'OK', 'THANKS', 'MUSCLE', 'CrossMark'}:
                raise failure('表情不在已验证的持久回执枚举内', '使用已支持的标准表情，其他表情保留原事件等待支持')
            original = self._original(refs[0])
            target = self._dependency_target(original, ancestors) if original else None
            if target is None:
                raise failure('表情目标尚未确认实际投递', '读取原消息的公开对应关系；表情会等待重试')
        actual = self.client.message_view(target, 'open_id')
        if actual is None or actual.get('chat_id') != self.chat:
            raise failure('自己的 bot 无法读取表情目标', '核对本应用对实际群消息的访问权限')
        digest = hashlib.sha256(json.dumps([target, emoji, deleting], separators=(',', ':')).encode()).hexdigest()
        previous = self.store.delivery_by_source(self.binding_id, event['id'], 'r2f', agent_id=self.agent)
        delivery = self.store.reserve_delivery(self.binding_id, event['id'], 'r2f', agent_id=self.agent,
            source_at=event['created_at'], content_hash=digest, stream='relay', root_id=refs[0], now=now)
        if delivery.status == 'acked':
            return delivery.target_id
        if previous and previous['status'] in ('pending', 'unknown', 'waiting_receipt'):
            # A durable reservation may have dispatched before a crash. Never
            # infer that an old pending row represents an unsent first attempt.
            self.store.fail_delivery(delivery.id, unknown=True, now=now)
            snapshot = self.client.own_reactions(target, emoji, expected_reaction_id=rid if deleting else "")
            if (snapshot.complete and snapshot.app_id == self.app and snapshot.message_id == target
                    and snapshot.emoji == emoji):
                if not deleting and len(snapshot.reaction_ids) == 1:
                    rid = snapshot.reaction_ids[0]
                    self.store.ack_outlet_delivery(delivery.id, target, self.app, reaction_id=rid, emoji=emoji, now=now)
                    return target
                if deleting and not snapshot.reaction_ids:
                    # The exact own reaction was previously ACKed. Complete
                    # current absence establishes the intended removed state.
                    with self.store.transaction():
                        self.store.ack_outlet_delivery(delivery.id, target, self.app, reaction_id=rid, emoji=emoji, now=now)
                        self.store.settle_delivery(old['id'], outcome='removed', now=now)
                    return target
            # No documented visibility interval proves that an unknown create
            # was unsent; even complete absence cannot authorize another POST.
            raise failure('表情操作结果仍未确认，已保留原记录', '继续读取自己应用的完整表情回执；未知结果不得再次写入')
        if delivery.status == 'failed':
            self.store.retry_delivery(delivery.id, now=now)
        if delivery.status not in ('pending', 'failed'):
            raise failure('表情操作仍需核实实际回执', '检查已确认表情，不要变更发送身份')
        # Crash/parse/network errors after dispatch cannot turn into an unsent
        # attempt. Only an explicit definite rejection makes it retryable.
        self.store.fail_delivery(delivery.id, unknown=True, now=now)
        try:
            if deleting and old['status'] != 'removed':
                # Reaction IDs are opaque and may carry canonical base64
                # padding. Quote each segment; keep the exact ID in the receipt.
                path = f'/open-apis/im/v1/messages/{quote(target, safe="")}/reactions/{quote(rid, safe="")}'
                self.client.call('withdraw own reaction', ['api', 'DELETE', path, '--as', 'bot'])
            elif not deleting:
                rid = self.client.react(target, emoji)
        except gs.CliError as exc:
            if exc.definite:
                self.store.fail_delivery(delivery.id, now=now)
            raise failure('自己的 bot 表情操作尚未确认', '读取自己应用的完整表情回执；未知结果不得重复写入') from None
        with self.store.transaction():
            self.store.ack_outlet_delivery(delivery.id, target, self.app, reaction_id=rid, emoji=emoji, now=now)
            if deleting:
                self.store.settle_delivery(old['id'], outcome='removed', now=now)
        return target

    def _acked_text_replay(self, event):
        """No-effect replay only; catch_up still verifies before successful return.

        This receipt never authorizes another effect or proves a new reply's
        root. Direct deliver/recovery and any attachment still use actual reads.
        """
        if (event.get('kind') not in (9, 40003) or not self._valid(event)
                or any(tag[:1] == ['imeta'] for tag in event['tags'])):
            return False
        if event['kind'] == 40003 and not gs.edit_target(event):return False
        row = self.store.delivery_by_source(self.binding_id,event['id'],
            'e2f' if event['kind'] == 40003 else 'b2f',agent_id=self.agent)
        if row is not None and event['kind'] == 40003 and row['status'] == 'skipped':
            return True  # Explicit superseded edit, not a fabricated native ACK.
        if row is None or row['status'] != 'acked':
            return False
        target = row['target_id']
        receipt = self.store.outlet_receipt(row['id'])
        return bool(isinstance(target,str) and gs.MESSAGE_ID_RE.fullmatch(target)
            and receipt and receipt['sender_app_id']==self.app
            and receipt['target_message_id']==target
            and not receipt['reaction_id'] and not receipt['emoji'])

    def _acked_reaction_replay(self, event, *, local_only=False):
        """Skip only a settled identical own effect; never authorize a write."""
        if event.get('kind') not in (5, 7):return False
        if local_only:
            # Exact own settled receipts can discard a scheduling hint without
            # a target HTTP lookup. No effect uses this path. A receipt commits
            # to the immutable source ID, binding, author, target and app below.
            hs=[t for t in event.get('tags',[]) if t[:1]==['h']]
            if not self._valid_envelope(event) or hs not in ([],[['h',self.channel]]):return False
        elif not self._valid(event):return False
        refs=[t[1] for t in event['tags'] if t[:1]==['e'] and len(t)>=2]
        if len(refs)!=1 or not gs.HEX64_RE.fullmatch(refs[0]):return False
        row=self.store.delivery_by_source(self.binding_id,event['id'],'r2f',agent_id=self.agent)
        if (row is None or row['status'] not in (('acked','removed') if event['kind']==7 else ('acked',))
                or row['root_id']!=refs[0]):return False
        receipt=self.store.outlet_receipt(row['id']);target=row['target_id']
        if (not receipt or receipt['sender_app_id']!=self.app or receipt['target_message_id']!=target
                or not isinstance(target,str) or not gs.MESSAGE_ID_RE.fullmatch(target)
                or not receipt['reaction_id'] or not receipt['emoji']):return False
        deleting=event['kind']==5;emoji=receipt['emoji']
        if deleting:
            old=self.store.delivery_by_source(self.binding_id,refs[0],'r2f',agent_id=self.agent)
            previous=self.store.outlet_receipt(old['id']) if old else None
            if (not old or old['status']!='removed' or not previous
                    or any(previous[key]!=receipt[key] for key in ('sender_app_id','target_message_id','reaction_id','emoji'))):return False
        elif gs.merged_reaction_map(self.round.cfg.get('reaction_map') or {}).get(gs._reaction_emoji(event['content']))!=emoji:
            return False
        digest=hashlib.sha256(json.dumps([target,emoji,deleting],separators=(',',':')).encode()).hexdigest()
        return row['content_hash']==digest

    def _retained_reaction_scope(self, event):
        """No-h feed frames are hints. Unknown targets cannot starve peers;
        exact signed foreign targets provide negative evidence only.
        """
        target = _one(event['tags'], 'e')
        if self._original(target) is not None:
            return 'local'
        originals = self._query([{'kinds': [9], 'ids': [target], 'limit': 2}], require_below_limit=False)
        # A full page or malformed/ambiguous h cannot prove exclusion.
        if (len(originals) == 1 and originals[0]['id'] == target
                and reaction_inbox.reject_foreign(self.store, self.binding_id,
                    self.channel, self.agent, event, originals[0], now=int(self.clock().timestamp()))):
            return 'foreign'
        return 'unknown'

    def _only_orphan_withdrawals(self, rows, by_id):
        """Classify scheduling only; absence never settles or authorizes a write."""
        if not rows or self.store.conn.execute(
                "SELECT 1 FROM delivery WHERE binding_id=? AND agent_id=? AND status IN ('pending','failed','unknown','waiting_receipt') LIMIT 1",
                (self.binding_id,self.agent)).fetchone():return False
        for row in rows:
            event=by_id.get(row['source_id'])
            if event is None or event['kind']!=5 or not self._valid_envelope(event):return False
            target=_one(event['tags'],'e')
            if (not target or not gs.HEX64_RE.fullmatch(target) or target in by_id
                    or self.store.delivery_by_source(self.binding_id,event['id'],'r2f',agent_id=self.agent) is not None
                    or self.store.delivery_by_source(self.binding_id,target,'r2f',agent_id=self.agent) is not None):return False
        return True

    def catch_up(self, *, max_events=None):
        """Bounded source attempts; signed exact-ID hints pin skipped history."""
        if max_events is not None and (type(max_events) is not int or max_events < 1):
            raise ValueError('invalid outlet slice')
        limit=min(max_events if max_events is not None else 8,8)
        self.slice_pending=False
        self.attempted_work=False
        verified_before=self._verification_count
        events=signed_history({'authors':[self.agent],'#h':[self.channel],'kinds':KINDS,'since':self._since()},
            relay=self.relay,key=self.key,http=self.http,clock=self.clock,auth_tag=self.auth,
            allow_unscoped_reactions=True)
        retained=reaction_inbox.pending(self.store,self.binding_id,self.channel,self.agent)
        ordered=reaction_inbox.causal_order([*events,*retained])
        retained_ids={e['id'] for e in retained}
        legacy={r['source_id']:r for r in outlet_pending.pending(self.store,self.binding_id,self.channel,self.agent)}
        queued={r['source_id']:r for r in outlet_work.pending(self.store,self.binding_id,self.channel,self.agent)}
        held_ids=set(legacy)|set(queued)|retained_ids
        by_id={e['id']:e for e in ordered}
        def settled(event):
            with self.store.transaction():
                outlet_work.forget(self.store,self.binding_id,self.agent,event['id'])
                outlet_pending.forget(self.store,self.binding_id,self.agent,event['id'])
                if event['id'] in retained_ids:reaction_inbox.forget(self.store,self.binding_id,self.agent,event['id'])
        candidates=[]
        for event in ordered:
            if not self._valid_envelope(event):
                raise failure('未决事件签名或作者无法确认','保留原记录，不推进游标')
            if self._acked_text_replay(event) or self._acked_reaction_replay(event,local_only=True):
                settled(event);continue
            direction='e2f' if event['kind']==40003 else 'b2f' if event['kind']==9 else 'r2f'
            prior=self.store.delivery_by_source(self.binding_id,event['id'],direction,agent_id=self.agent)
            if event['id'] not in held_ids and event['created_at']<self.initial_since and prior is None:
                continue
            if event['kind']==5 and event['id'] not in held_ids and not any(t[:1]==['h'] for t in event['tags']):
                ref=_one(event['tags'],'e')
                if not ref or not gs.HEX64_RE.fullmatch(ref):
                    raise failure('撤回引用格式无法核验','保留原记录，不处理不完整撤回')
                previous=self.store.delivery_by_source(self.binding_id,ref,'r2f',agent_id=self.agent) if ref and gs.HEX64_RE.fullmatch(ref) else None
                if previous is None and by_id.get(ref,{}).get('kind')!=7:continue
            candidates.append(event)
        # These are untrusted scheduling hints only. Persist every skipped ID
        # atomically before a newer ACK can move the normal replay cursor.
        outlet_work.capture(self.store,self.binding_id,self.channel,self.agent,candidates,
            floor=self.initial_since,now=int(self.clock().timestamp()),retained=retained_ids)
        attempted=0;deadline=time.monotonic()+5.0;failed=False;actual_failure=None
        while attempted<limit and time.monotonic()<deadline:
            now=int(self.clock().timestamp())
            choice=outlet_work.choose(self.store,self.binding_id,self.channel,self.agent,ordered,now=now)
            if choice is None:break
            row,turn=choice;event=by_id[row['source_id']]
            outlet_work.start(self.store,self.binding_id,self.agent,row,turn,now=now)
            attempted+=1  # Scope/root failures consume the same unit as ACKs.
            self.attempted_work=True
            trace=getattr(self.round,'latency_trace',None)
            if trace is not None:trace.record(self.binding_id,'outlet_attempt_started',source=event['id'],target=self.agent,kind=event['kind'])
            try:
                if event['kind'] in (5,7):
                    if reaction_inbox.foreign_reference(self.store,self.binding_id,self.channel,self.agent,event):
                        settled(event);continue
                    if event['kind']==7:
                        scope=self._retained_reaction_scope(event)
                        if scope!='local':
                            if scope=='foreign':settled(event)
                            else:failed=True
                            continue
                    else:
                        previous=self.store.delivery_by_source(self.binding_id,_one(event['tags'],'e'),'r2f',agent_id=self.agent)
                        receipt=self.store.outlet_receipt(previous['id']) if previous else None
                        if receipt is None or receipt['sender_app_id']!=self.app:
                            failed=True;continue
                result=(self._deliver(event,frozenset(),retained=True) if event['id'] in held_ids else self.deliver(event))
                direction='e2f' if event['kind']==40003 else 'b2f' if event['kind']==9 else 'r2f'
                saved=self.store.delivery_by_source(self.binding_id,event['id'],direction,agent_id=self.agent)
                if saved and saved['status'] in ('acked','removed','skipped'):
                    settled(event)
                elif result is None and event['kind']==40003:
                    settled(event)  # Fresh signed superseding edit, not an ACK.
                else:failed=True
            except gs.GroupSyncError as error:
                if not self.has_local_grant():raise
                actual_failure=actual_failure or error
                failed=True  # Keep actual proof/read failures distinct from scheduling.
            finally:
                if trace is not None:trace.record(self.binding_id,'outlet_attempt_finished',source=event['id'],target=self.agent,kind=event['kind'])
        remaining=outlet_work.pending(self.store,self.binding_id,self.channel,self.agent)
        old=outlet_pending.pending(self.store,self.binding_id,self.channel,self.agent)
        if actual_failure is not None:raise actual_failure
        if not old and self._only_orphan_withdrawals(remaining,by_id):
            # Even a no-effect wait requires current own authority; a revoked
            # policy or failed read remains an error, never a healthy wait.
            if self._verification_count==verified_before:self.verify()
            raise OutletWaiting(max(int(self.clock().timestamp())+1,min(r['retry_at'] for r in remaining)))
        if failed or old or (remaining and outlet_work.choose(self.store,self.binding_id,self.channel,self.agent,ordered,
                now=int(self.clock().timestamp())) is None):
            raise OutletDeferred()
        if remaining:
            self.slice_pending=True
            return len(events)
        if self._verification_count==verified_before:self.verify()
        return len(events)

    def _since(self):
        since = (self.store.replay_since(self.binding_id, 'relay', agent_id=self.agent)
                 if self.store.has_replay_state(self.binding_id, 'relay', agent_id=self.agent)
                 else self.initial_since)
        rows = outlet_pending.pending(self.store, self.binding_id, self.channel, self.agent)
        rows += outlet_work.pending(self.store, self.binding_id, self.channel, self.agent)
        return min([since, *(row['source_at'] for row in rows)])

    async def follow(self, on_event, on_status, *, connect=None, on_reconnect=None):
        """Feed only this agent/channel. Caller serializes writes with its worker.

        Default connection uses the existing redirect-protected WebSocket
        adapter. Injecting a connector is limited to fake/local transports.
        """
        if connect is None:
            try:
                from .relay_feed import _connect as connect
            except ImportError:
                from relay_feed import _connect as connect
        url = self.relay.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
        backoff = 1.0
        def status(value):
            try: on_status(value)
            except Exception: pass
        while True:
            connected_at = None
            try:
                await asyncio.to_thread(self.verify)
                async with connect(url) as ws:
                    sub, auth_id, authed, subscribed = 'o' + secrets.token_hex(4), None, False, False
                    deadline = asyncio.get_running_loop().time() + 30
                    iterator = ws.__aiter__()
                    try:
                        while True:
                            remaining = deadline - asyncio.get_running_loop().time()
                            raw = await iterator.__anext__() if authed else await asyncio.wait_for(iterator.__anext__(), max(0, remaining))
                            try:
                                if not isinstance(raw, (str, bytes)) or len(raw) > 2 ** 22:
                                    continue
                                frame = json.loads(raw)
                                if not isinstance(frame, list) or not frame or not isinstance(frame[0], str):
                                    continue
                            except (ValueError, UnicodeError, RecursionError):
                                continue
                            if frame[0] == 'AUTH' and not authed:
                                if len(frame) != 2 or not isinstance(frame[1], str) or not 0 < len(frame[1]) <= 4096:
                                    continue
                                signed = gs.sign_event(self.key, 22242, [['relay', url], ['challenge', frame[1]], self.auth], '', int(self.clock().timestamp()))
                                auth_id = signed['id']
                                await ws.send(json.dumps(['AUTH', signed]))
                            elif frame[0] == 'OK' and not authed and len(frame) == 4 and auth_id and frame[1] == auth_id:
                                if frame[2] is not True:
                                    break
                                since = self._since()
                                await ws.send(json.dumps(['REQ', sub, {'authors': [self.agent], '#h': [self.channel], 'kinds': KINDS, 'since': since}]))
                                authed = subscribed = True
                                connected_at = time.monotonic()
                                status('connected')
                                if on_reconnect:
                                    await on_reconnect()
                                else:
                                    await asyncio.to_thread(self.catch_up)
                            elif frame[0] == 'EVENT' and authed and len(frame) == 3 and frame[1] == sub:
                                if await thread_call(self._valid, frame[2]):
                                    await on_event(frame[2])
                            elif frame[0] == 'CLOSED' and authed and len(frame) == 3 and frame[1] == sub:
                                break
                    finally:
                        if subscribed:
                            try: await asyncio.wait_for(ws.send(json.dumps(['CLOSE', sub])), 1)
                            except Exception: pass
            except Exception:
                status(str(failure('agent 的 relay 订阅断开', '检查自己的密钥与网络，正在按原游标重连')))
            if connected_at is not None and time.monotonic() - connected_at >= 60:
                backoff = 1.0
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)
