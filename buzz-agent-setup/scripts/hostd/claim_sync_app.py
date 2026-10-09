"""Local sync application attestation, separate from card approval or grants.

Only the caller's reviewed local bot/profile and fresh signed channel authority
may populate this binding field. No foreign policy supplies local credentials.
"""
from __future__ import annotations

from pathlib import Path
import recovery_authority as authority
import buzz_feishu_group_sync as gs
try:
    from .bot_clients import BotLarkCli, READ_SCOPE_GROUPS, failure
    from .signed_reads import _event_result_shape
except ImportError:
    from bot_clients import BotLarkCli, READ_SCOPE_GROUPS, failure
    from signed_reads import _event_result_shape


def pending():
    return failure('同步应用的本机职责尚未核验', '核对选定 bot 的应用目录、权限、空条件身份背书与当前群频道成员；保留未决发布账本')


def field(app_id):
    if not isinstance(app_id, str) or not gs.APP_ID_RE.fullmatch(app_id):
        raise pending()
    return {'version': 1, 'app_id': app_id}


def matches(value, app_id):
    return (isinstance(value, dict) and set(value) == {'version', 'app_id'}
            and type(value['version']) is int and value['version'] == 1
            and value['app_id'] == app_id)


def bot(client, app_id, config_dir, data_dir, chat_id):
    """Real selected adapter, exact reviewed paths, current app and full roster."""
    try:
        if (not isinstance(client, BotLarkCli) or client.app_id != app_id
                or client.config_dir != Path(config_dir) or client.data_dir != Path(data_dir)
                or client.identity() != (app_id, '')):
            raise pending()
        scopes = client.bot_scopes()
        if any(not choices & scopes for choices in READ_SCOPE_GROUPS.values()):
            raise pending()
        listing = client.member_listing(chat_id, 'union_id')
        if not isinstance(listing, gs.MemberListing) or listing.complete is not True or app_id not in listing.bots:
            raise pending()
        return listing
    except Exception:
        raise pending() from None


def event(value, kind, now):
    try:
        if (not _event_result_shape(value) or value['kind'] != kind
                or value['created_at'] > now + gs.RELAY_CLOCK_SKEW_SECONDS
                or len(value['content'].encode()) > 2**20 or len(value['tags']) > 4096
                or any(len(t) > 64 or any(len(v.encode()) > 8192 for v in t) for t in value['tags'])
                or sum(len(v.encode()) for t in value['tags'] for v in t) > 2**18
                or not gs._nip01_event_verified(value)):
            raise pending()
        return value
    except Exception:
        raise pending() from None


def profiles_and_app(profiles, policies, agent, mirror, owner, app_id, now):
    """Verify latest own/mirror empty OA and latest owner-signed own app policy."""
    try:
        if not isinstance(profiles, list) or len(profiles) > 256:
            raise pending()
        for value in profiles:
            event(value, 0, now)
            if value['pubkey'] not in (agent, mirror):
                raise pending()
        for pub in (agent, mirror):
            head = authority.latest([e for e in profiles if e['pubkey'] == pub])
            if (head is None or authority.attested_owner(head, pub) != owner
                    or authority.tags(head, 'auth')[0][2] != ''):
                raise pending()
        if not isinstance(policies, list) or len(policies) > 256:
            raise pending()
        for value in policies:
            event(value, 30177, now)
            if value['pubkey'] != owner:
                raise pending()
            authority.exact_tag(value, 'd', agent)
        head = authority.latest(policies)
        if head is None:
            raise pending()
        feishu = authority.policy_body(head, agent).get('feishu')
        if not isinstance(feishu, dict) or feishu.get('app_id') != app_id or feishu.get('mirror') is True:
            raise pending()
    except Exception:
        raise pending() from None


def roster(rows, pin, channel, agent, mirror, owner, now):
    try:
        if not isinstance(pin, str) or not gs.HEX64_RE.fullmatch(pin):
            raise pending()
        if not isinstance(rows, list) or not rows or len(rows) > 256:
            raise pending()
        for value in rows:
            event(value, 39002, now)
            if value['pubkey'] != pin:
                raise pending()
            authority.exact_tag(value, 'd', channel)
        roles = authority.membership(authority.latest(rows), channel)
        if roles.get(agent) != 'bot' or roles.get(mirror) != 'bot' or roles.get(owner) not in ('owner', 'admin'):
            raise pending()
        return roles
    except Exception:
        raise pending() from None


def binding_entry(policies, mirror, owner, channel, ref, now):
    """Read the exact full signed entry; BindingClaim deliberately drops fields."""
    try:
        candidates = []
        for value in policies:
            if value.get('kind') == 30177 and value.get('pubkey') == owner and authority.tags(value, 'd') == [['d', mirror]]:
                candidates.append(event(value, 30177, now))
        head = authority.latest(candidates)
        if head is None:
            return None
        doc = authority.policy_body(head, mirror)
        feishu = doc.get('feishu')
        if not isinstance(feishu, dict) or feishu.get('mirror') is not True or 'app_id' in feishu:
            raise pending()
        entries = feishu.get('bindings', [])
        if not isinstance(entries, list) or any(not isinstance(v, dict) for v in entries):
            raise pending()
        matches = [v for v in entries if v.get('channel') == channel and v.get('chat_ref') == ref]
        if len(matches) > 1:
            raise pending()
        return matches[0] if matches else None
    except Exception:
        raise pending() from None
