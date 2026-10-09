"""Pure validation of the unpublished hostd remote approval wire contract v1.

Input completeness is an internal upstream query assertion, not something this
parser proves. Proofs contain metadata only: they neither grant membership nor
activate a foreign binding, and do not replace local allowlist/bot readback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re

import buzz_feishu_group_sync as gs
import recovery_authority as authority
from .signed_reads import _event_result_shape

PREFIX = 'hostd-card-approval-v1'
MAX_TIME = 2**63 - 1
NOTICE = ('远端卡片批准证明尚未核验，保留待处理。怎么解决：检查完整签名读取、当前身份、应用、频道成员与胜出的绑定认领。'
          '\n复制给 AI：帮我核查 hostd 远端卡片批准证明；不要把签名记录当作已授权或已接入，也不要猜测同步应用。')


class ApprovalPending(ValueError):
    status = 'pending'

    def __init__(self):
        super().__init__(NOTICE)


@dataclass(frozen=True)
class ApprovalScope:
    agent_pubkey: str
    agent_owner_pubkey: str
    app_id: str
    channel_id: str
    chat_ref: str
    mirror_pubkey: str
    mirror_owner_pubkey: str
    claimed_at: int


@dataclass(frozen=True)
class ProofContext:
    scope: ApprovalScope
    relay_pubkey: str
    agent_profile: dict = field(repr=False)
    agent_policy: dict = field(repr=False)
    mirror_profiles: tuple = field(repr=False)
    mirror_policies: tuple = field(repr=False)
    roster: dict = field(repr=False)
    now: int
    complete: bool = False


@dataclass(frozen=True)
class DecodedApproval:
    scope: ApprovalScope
    record_id: str
    content_hash: str
    request_id: str
    request_created_at: int
    request_deadline: int
    card_generation: int
    card_message_sha256: str
    decision_event_sha256: str
    decision_at: int
    published_at: int


@dataclass(frozen=True)
class ApprovalProof(DecodedApproval):
    """Current signed authority evidence, not a local grant or activation."""


@dataclass(frozen=True)
class SyncAppProof:
    app_id: str
    mirror_pubkey: str
    mirror_owner_pubkey: str
    channel_id: str
    chat_ref: str
    claimed_at: int
    policy_id: str
    checked_at: int


_SCOPE_KEYS = tuple(ApprovalScope.__dataclass_fields__)
_BODY_KEYS = frozenset((*_SCOPE_KEYS, 'version', 'decision', 'request_id',
                       'request_created_at', 'request_deadline', 'card_generation',
                       'card_message_sha256', 'decision_event_sha256', 'decision_at'))


def _need(value):
    if not value:
        raise ApprovalPending()


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_TIME


def _hex(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _app(value):
    return isinstance(value, str) and len(value) <= 256 and gs.APP_ID_RE.fullmatch(value) is not None


def _json(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            _need(key not in out)
            out[key] = value
        return out

    def constant(_):
        raise ApprovalPending()

    body = json.loads(raw, object_pairs_hook=unique, parse_constant=constant)
    _need(isinstance(body, dict))
    return body


def _scope(scope):
    _need(type(scope) is ApprovalScope)
    _need(all(_hex(getattr(scope, name)) for name in
              ('agent_pubkey', 'agent_owner_pubkey', 'chat_ref', 'mirror_pubkey', 'mirror_owner_pubkey')))
    _need(_app(scope.app_id) and isinstance(scope.channel_id, str)
          and gs.UUID_RE.fullmatch(scope.channel_id) and _integer(scope.claimed_at))
    _need(scope.agent_pubkey != scope.agent_owner_pubkey
          and scope.mirror_pubkey != scope.mirror_owner_pubkey)


def _event(event, kind, now):
    _need(_integer(now) and _event_result_shape(event) and event['kind'] == kind)
    # Same byte/structure caps as the strict relay event boundary. Verify shape
    # before the legacy crypto oracle, which intentionally remains unchanged.
    _need(len(event['content']) <= 2**20 and len(event['tags']) <= 4096)
    _need(all(len(tag) <= 64 and all(len(item) <= 8192 for item in tag) for tag in event['tags']))
    _need(len(event['content'].encode('utf-8')) <= 2**20
          and sum(len(item.encode('utf-8')) for tag in event['tags'] for item in tag) <= 2**18
          and all(len(item.encode('utf-8')) <= 8192 for tag in event['tags'] for item in tag))
    _need(event['created_at'] <= now + gs.RELAY_CLOCK_SKEW_SECONDS and gs._nip01_event_verified(event))


def _owner(profile, pubkey, now):
    _event(profile, 0, now)
    _need(profile['pubkey'] == pubkey)
    _json(profile['content'])
    owner = authority.attested_owner(profile, pubkey)
    _need(authority.tags(profile, 'auth')[0][2] == '')
    return owner


def _policy(event, pubkey, now):
    _event(event, 30177, now)
    # policy_body rejects duplicate JSON fields and ambiguous d tags.
    return authority.policy_body(event, pubkey)


def decode(event, *, now):
    """Verify the record wire shape/signature only, without current authority."""
    try:
        _event(event, 30078, now)
        body = _json(event['content'])
        _need(set(body) == _BODY_KEYS)
        _need(type(body['version']) is int and body['version'] == 1 and body['decision'] == 'approve')
        _need(event['content'] == json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False))
        scope = ApprovalScope(**{name: body[name] for name in _SCOPE_KEYS})
        _scope(scope)
        _need(event['pubkey'] == scope.mirror_pubkey)
        _need(isinstance(body['request_id'], str) and re.fullmatch('JOIN-[0-9a-f]{8}', body['request_id']))
        _need(all(_integer(body[name]) for name in ('request_created_at', 'request_deadline', 'decision_at')))
        _need(_integer(body['card_generation'], 1)
              and _hex(body['card_message_sha256']) and _hex(body['decision_event_sha256']))
        _need(body['request_deadline'] == body['request_created_at'] + 7 * 86400
              and body['request_created_at'] <= body['decision_at'] <= body['request_deadline']
              and scope.claimed_at <= body['decision_at'] <= event['created_at'])
        digest = hashlib.sha256(event['content'].encode('utf-8')).hexdigest()
        expected = {'t': PREFIX, 'h': scope.channel_id, 'p': scope.agent_pubkey, 'd': PREFIX + ':' + digest}
        for name, value in expected.items():
            authority.exact_tag(event, name, value)
        _need(all(tag[0] in (*expected, 'auth') for tag in event['tags']))
        auth = authority.tags(event, 'auth')
        if auth:
            _need(authority.attested_owner(event, scope.mirror_pubkey) == scope.mirror_owner_pubkey and auth[0][2] == '')
        return DecodedApproval(scope, event['id'], digest, body['request_id'], body['request_created_at'],
                               body['request_deadline'], body['card_generation'], body['card_message_sha256'],
                               body['decision_event_sha256'], body['decision_at'], event['created_at'])
    except Exception:
        raise ApprovalPending() from None


def _current(context):
    """Validate all supplied candidates, then select the latest owner policies.

    Trusted upstream must enumerate the complete candidate query. A hand-picked
    winner cannot provide that guarantee merely by constructing this DTO.
    """
    _need(type(context) is ProofContext and context.complete is True)
    _scope(context.scope)
    _need(_integer(context.now) and _hex(context.relay_pubkey))
    scope, now = context.scope, context.now
    _need(_owner(context.agent_profile, scope.agent_pubkey, now) == scope.agent_owner_pubkey)
    agent_body = _policy(context.agent_policy, scope.agent_pubkey, now)
    _need(context.agent_policy['pubkey'] == scope.agent_owner_pubkey)
    agent_feishu = agent_body.get('feishu')
    _need(isinstance(agent_feishu, dict) and agent_feishu.get('app_id') == scope.app_id
          and agent_feishu.get('mirror') is not True)
    _event(context.roster, 39002, now)
    _need(context.roster['pubkey'] == context.relay_pubkey)
    roles = authority.membership(context.roster, scope.channel_id)
    _need(roles.get(scope.agent_pubkey) == 'bot')
    _need(isinstance(context.mirror_profiles, (tuple, list)) and context.mirror_profiles
          and isinstance(context.mirror_policies, (tuple, list)) and context.mirror_policies)
    _need(len(context.mirror_profiles) <= 4096 and len(context.mirror_policies) <= 4096)
    profiles = {}
    for profile in context.mirror_profiles:
        _event(profile, 0, now)
        _owner(profile, profile['pubkey'], now)
        profiles.setdefault(profile['pubkey'], []).append(profile)
    owners = {pk: _owner(authority.latest(rows), pk, now) for pk, rows in profiles.items()}
    parsed = []
    candidates = {scope.mirror_pubkey}
    for policy in context.mirror_policies:
        _event(policy, 30177, now)
        tag = authority.tags(policy, 'd')
        _need(len(tag) == 1 and len(tag[0]) == 2 and _hex(tag[0][1]))
        pubkey = tag[0][1]
        body = _policy(policy, pubkey, now)
        feishu = body.get('feishu')
        if isinstance(feishu, dict) and feishu.get('mirror') is True:
            candidates.add(pubkey)
        parsed.append((pubkey, policy, body))
    _need(candidates <= set(owners))
    claims = []
    for mirror in sorted(candidates):
        owner = owners[mirror]
        head = authority.latest([policy for pk, policy, _ in parsed if pk == mirror and policy['pubkey'] == owner])
        _need(head is not None)
        body = _policy(head, mirror, now)
        feishu = body.get('feishu')
        _need(isinstance(feishu, dict))
        if feishu.get('mirror') is not True:
            continue  # Current owner revocation overrides older declarations.
        bindings = feishu.get('bindings')
        _need(isinstance(bindings, list) and len(bindings) <= 4096)
        decoded = gs._claims_in(mirror, owner, '', head['content'])
        _need(len(decoded) == len(bindings))
        _need(len({(c.channel, c.chat_ref) for c in decoded}) == len(decoded))
        for claim, entry in zip(decoded, bindings):
            _need(claim.claimed_at <= claim.heartbeat and claim.claimed_at <= now + gs.RELAY_CLOCK_SKEW_SECONDS)
            if gs.claim_valid(claim, now):
                claims.append((claim, entry, head['id']))
    eligible = [row for row in claims if row[0].channel == scope.channel_id
                and roles.get(row[0].mirror) == 'bot' and roles.get(row[0].owner) in ('owner', 'admin')]
    _need(eligible)
    winner = gs._strongest([row[0] for row in eligible])
    chosen = next(row for row in eligible if row[0] is winner)
    _need(not any(c.channel != scope.channel_id and c.chat_ref == winner.chat_ref and gs.claim_beats(c, winner)
                  for c, _, _ in claims))
    _need((winner.mirror, winner.owner, winner.chat_ref, winner.claimed_at) ==
          (scope.mirror_pubkey, scope.mirror_owner_pubkey, scope.chat_ref, scope.claimed_at))
    return chosen


def verify(event, context):
    """Revalidate current signed scope; return metadata, never a grant."""
    try:
        _need(type(context) is ProofContext)
        decoded = decode(event, now=context.now)
        _need(decoded.scope == context.scope)
        _current(context)
        return ApprovalProof(**{name: getattr(decoded, name) for name in DecodedApproval.__dataclass_fields__})
    except Exception:
        raise ApprovalPending() from None


def sync_app(context):
    """Read only binding.sync_app from the current verified winning claim."""
    try:
        claim, entry, policy_id = _current(context)
        declaration = entry.get('sync_app')
        _need(isinstance(declaration, dict) and set(declaration) == {'version', 'app_id'}
              and type(declaration['version']) is int and declaration['version'] == 1 and _app(declaration['app_id']))
        return SyncAppProof(declaration['app_id'], claim.mirror, claim.owner, claim.channel,
                            claim.chat_ref, claim.claimed_at, policy_id, context.now)
    except Exception:
        raise ApprovalPending() from None
