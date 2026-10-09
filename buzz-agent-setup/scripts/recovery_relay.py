"""Owner-side signed relay transport. No invitation state or Feishu API calls.

Reuses the existing bounded NIP-01/NIP-98 wire primitives (currently housed in
the mirror module). Owner credentials remain inside this deterministic process.
"""
import datetime as dt
import json
import re
import urllib.error
import uuid

import buzz_feishu_group_sync as wire
import recovery_authority as authority
import recovery_inventory
from recovery_origin import canonical_origin
from recovery_read_budget import SourceReadFailure, read_budget


class RouteBlocked(ValueError):
    """Known safe reason; notices are allowed only with verified owner access."""
    def __init__(self, code, reason, *, notice_allowed=False):
        super().__init__(code)
        self.code, self.reason, self.notice_allowed = code, reason, notice_allowed


def _tag_values(event, name):
    return [tag[1] for tag in event["tags"] if len(tag) >= 2 and tag[0] == name]


class RecoveryRelay:
    def __init__(self, origin, owner_key, relay_pubkey, *, http=wire._http_get, now=None):
        self.origin = canonical_origin(origin)
        if not isinstance(relay_pubkey, str) or not re.fullmatch(r"[0-9a-f]{64}", relay_pubkey):
            raise ValueError("relay signer pin required")
        self.key = wire.secret_hex(owner_key, "recovery owner")
        self.owner = wire._signer_pubkey(self.key)
        self.relay_pubkey, self.http = relay_pubkey, http
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    def query(self, filters):
        # Only reads are interruptible. SQLite commits and publication remain
        # outside this scope; a trickling response cannot retain the round lock.
        with read_budget() as budget:
            return self._query(filters, budget)

    def _query(self, filters, budget):
        return self._signed_query(filters, budget, 256)

    def _policy_snapshot(self, budget):
        # Relay 0.2.1 advertises/clamps max_limit=1000. A full response is
        # ambiguous: never turn a truncated public claim set into absence.
        rows = self._signed_query([{'kinds': [30177], 'limit': 1000}], budget, 1000)
        if len(rows) >= 1000:
            raise ValueError('incomplete policy snapshot')
        return rows

    def _signed_query(self, filters, budget, capacity):
        url = self.origin + "/query"
        body = json.dumps(filters, separators=(",", ":")).encode()
        headers = {"Authorization": wire.nip98_header(self.key, "POST", url, self.now(), body=body),
                   "Content-Type": "application/json", "Accept": "application/json"}
        try:
            status, answer = self.http(url, headers, budget.request(), body=body)
        except TimeoutError:
            # Socket inactivity can expire just before the wall alarm. It is
            # still a complete-read timeout, not one denied original author.
            budget.fail("source_read_timeout")
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                budget.fail("source_read_timeout")
            raise
        budget.response(len(answer))
        if status != 200 or len(answer) > 1024 * 1024:
            raise ValueError("recovery relay read unavailable")
        rows = json.loads(answer)
        if not isinstance(rows, list) or len(rows) > capacity or not all(wire._nip01_event_verified(e) for e in rows):
            raise ValueError("unverified relay response")
        return rows

    def lookup(self, event_id):
        rows = self.query([{"ids": [event_id], "limit": 2}])
        if len(rows) > 1 or any(row["id"] != event_id for row in rows):
            raise ValueError("recovery event lookup mismatch")
        return rows[0] if rows else None

    def sign(self, content, tags):
        return wire.sign_event(self.key, 9, tags, content, int(self.now().timestamp()))

    def publish(self, event):
        return wire.publish_signed_event(self.origin, self.key, event, self.http, self.now())

    def _members(self, channel):
        events = self.query([{"kinds": [39002], "authors": [self.relay_pubkey], "#d": [channel], "limit": 2}])
        if not events or any(e["kind"] != 39002 or e["pubkey"] != self.relay_pubkey
                             or _tag_values(e, "d") != [channel] for e in events):
            raise ValueError("authoritative membership unavailable")
        return authority.membership(authority.latest(events), channel)

    def _owner_access(self, roles):
        if roles.get(self.owner) not in ("owner", "admin", "member"):
            raise RouteBlocked("owner_not_member", "owner 当前无权在本群发送消息；请先恢复访问权限。")

    def validate_source(self, source, agent, policy):
        """Re-read the original signature and CURRENT requester authority.

        The continue sender's owner key supplies no authority to the original
        request. Feishu mirrors keep their signed mirror identity; display text
        and claimed human names never substitute for a verifiable principal.
        """
        recovery_inventory.source(source)
        policy = authority.runtime_policy(policy)
        if policy["owner"] != self.owner:
            raise ValueError("actual owner mismatch")
        channel, root = recovery_inventory.route(source["route"])
        roles = self._members(channel)
        self._owner_access(roles)
        try:
            event = self.lookup(source["event_id"])
            if (event is None or event["pubkey"] != source["signed_author"]
                    or authority.canonical_thread(event, channel) != root):
                raise ValueError("original signed event mismatch")
            delegated = authority.workflow_author(event, self.relay_pubkey, agent)
            actor = delegated or event["pubkey"]
            expected_kind = "relay-workflow" if actor != event["pubkey"] else "signed-event"
            if (source["kind"] != expected_kind
                    or actor != source.get("effective_author", source["signed_author"])):
                raise ValueError("original attribution mismatch")
        except SourceReadFailure:
            raise
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            raise RouteBlocked("source_unverified", "原请求的签名、来源身份或话题归属暂时无法核实。请 owner 检查 Relay 访问、原消息和恢复记录；修复后会自动重试，不会改用 owner 身份执行。", notice_allowed=True) from exc
        if roles.get(actor) not in ("owner", "admin", "member", "bot"):
            raise RouteBlocked("source_not_member", "原请求人当前已不在本群，或只有只读权限。请 owner 确认是否恢复其群权限；系统会保留任务并重试，不会自动加人或借用 owner 权限。", notice_allowed=True)
        mode = policy["respond_to"]
        allowed = mode != "nobody" and (mode == "anyone" or actor == self.owner
                                        or mode == "allowlist" and actor in policy["allowlist"])
        if not allowed and mode == "owner-only":
            # Recognizes the owner acting under an alternate, cryptographically
            # attested identity -- it is still exactly the owner, since only
            # the owner's own key can produce that signature. allowlist mode
            # promises a specific, closed pubkey set; it must not accept this
            # as a side door for a pubkey the owner never actually listed
            # (code review P1 on !1043).
            profiles = self.query([{"kinds": [0], "authors": [actor], "limit": 2}])
            profile = authority.latest([e for e in profiles if e["kind"] == 0 and e["pubkey"] == actor])
            try:
                allowed = profile is not None and authority.attested_owner(profile, actor) == self.owner
            except ValueError:
                allowed = False
        if not allowed:
            raise RouteBlocked("source_policy_denied", "原请求人不在 Agent 当前允许应答的人员范围内。请 owner 核对仅限 owner／指定人员的应答设置，或该 Agent 的有效身份背书；系统不会因自动 continue 而扩大权限。", notice_allowed=True)
        return actor

    def validate_route(self, channel, root, agent):
        if (str(uuid.UUID(channel)) != channel or not re.fullmatch(r"[0-9a-f]{64}", root)
                or not re.fullmatch(r"[0-9a-f]{64}", agent)):
            raise ValueError("invalid recovery route identity")
        event = self.lookup(root)
        if not event or authority.canonical_thread(event, channel) != root:
            raise ValueError("original Thread binding not verified")
        roles = self._members(channel)
        self._owner_access(roles)
        if agent not in roles:
            raise RouteBlocked("agent_not_member", "Agent 已不在本群。请 owner 确认是否重新邀请并完成审批；系统不会自动加人或扩大权限。", notice_allowed=True)
        if roles[agent] != "bot":
            raise RouteBlocked("agent_not_bot", "Agent 在本群不是机器人角色，暂不能自动续接。请 owner 检查邀请和成员角色；系统不会自动改权限。", notice_allowed=True)
        events = self.query(wire.directory_filters([agent]))
        profile = authority.latest([e for e in events if e["kind"] == 0 and e["pubkey"] == agent])
        policy = authority.latest([e for e in events if e["kind"] == 30177 and e["pubkey"] == self.owner
                                   and agent in _tag_values(e, "d")])
        try:
            if profile is None or policy is None or authority.attested_owner(profile, agent) != self.owner:
                raise ValueError("owner proof unavailable")
            mode = authority.policy_body(policy, agent).get("respond_to")
        except ValueError as exc:
            raise RouteBlocked("agent_owner_unverified", "Agent 所有者或公开策略无法核实。请 owner 检查身份背书和策略发布配置，修复后会自动重试。", notice_allowed=True) from exc
        if mode not in ("anyone", "owner-only", "allowlist"):
            raise RouteBlocked("agent_not_responding", "Agent 当前不接受群内请求，或响应策略无法确认。请 owner 检查 respond-to 配置；系统不会绕过安全策略。", notice_allowed=True)
        return mode
