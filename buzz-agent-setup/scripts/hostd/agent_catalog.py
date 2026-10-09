"""Protected local agent catalog from the existing version-1 join schema.

This loader never enables an agent or queries a relay. An own_bot_verified
record establishes local identity, bot credentials and absence from the old
join writer's actual configuration. Fresh signed relay policy/roster validation
is still required by the root resolver before runtime adapters are enabled.
No directory scans, guessed profiles, global people reads or secret outputs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import stat

import buzz_agent_join_requests as join
import gitlab_buzz_sync as sync
import recovery_authority as authority
from .safety import read_owned

NOTICE = ('本机 agent 目录无法安全核验。怎么解决：检查冻结的 agent 配置、旧入群配置的实际排除读回、自己的签名身份、独立应用凭据及运行文件。'
          '\n复制给 AI：帮我检查 hostd 本机 agent 目录和旧入群任务移交证据；不要输出密钥、应用 secret、人员资料或提示词正文。')


class CatalogError(ValueError):
    """Fixed, non-sensitive configuration failure."""


@dataclass(frozen=True)
class AgentRecord:
    name: str
    env_file: Path
    unit: str
    owner_pubkey: str
    pubkey: str | None
    channels: tuple[str, ...]
    prompt_file: Path | None
    responsible_config: Path | None
    log_file: Path | None
    capability_summary: str
    repos: tuple[str, ...]
    app_id: str | None
    lark_config_dir: Path | None
    lark_data_dir: Path | None
    env_sha256: str | None
    profile_sha256: str | None
    local_bot_verified: bool
    status: str
    reasons: tuple[str, ...]
    requires_fresh_relay: bool = True


@dataclass(frozen=True)
class Catalog:
    records: tuple[AgentRecord, ...]
    catalog_path: Path
    legacy_join_path: Path
    catalog_sha256: str
    legacy_join_sha256: str


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _document(raw, *, allow_empty=False):
    try:
        doc = json.loads(raw)
        # Old join legitimately has no remaining agents after a complete handoff.
        # Validate every original top-level field against the original schema,
        # substituting a schema-only row solely for its nonempty-list constraint.
        checked = doc
        if allow_empty and isinstance(doc, dict) and doc.get('agents') == []:
            checked = dict(doc, agents=[{'name': 'schema-only', 'env_file': '/schema-only.env',
                                        'unit': 'schema-only.service',
                                        'capabilities': {'summary': 'schema validation', 'repos': []}}])
        join.validate_config(checked)
        return doc
    except Exception:
        raise CatalogError(NOTICE) from None


def _path(value):
    if (not isinstance(value, str) or not value or not Path(value).is_absolute()
            or '..' in Path(value).parts or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError
    return Path(value)


def _runtime_file(path):
    """Check prompt/responsible metadata only; never read their contents."""
    directory = fd = None
    try:
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            os.close(directory); directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) & 0o022:
            raise ValueError
    finally:
        if fd is not None: os.close(fd)
        if directory is not None: os.close(directory)


def _identities(a):
    values = {('name', a['name']), ('env_file', str(_path(a['env_file']))), ('unit', a['unit'])}
    if 'feishu' in a:
        values.add(('app_id', a['feishu']['app_id']))
        for field in ('lark_config_dir', 'lark_data_dir'):
            values.add((field, str(_path(a['feishu'][field]))))
    return values


def _record(a, owner, legacy_ids):
    env_file = _path(a['env_file']); block = a.get('feishu')
    app = block['app_id'] if block else None
    cfg = _path(block['lark_config_dir']) if block else None
    data = _path(block['lark_data_dir']) if block else None
    reasons = []; pub = None; channels = (); prompt = responsible = None
    env_hash = profile_hash = None; bot_verified = False
    try:
        raw = read_owned(env_file); env_hash = _digest(raw)
        env = join.parse_env(raw.decode())
        pub = sync.publisher_pubkey_from_private_key(env.get('BUZZ_PRIVATE_KEY'))
        auth = json.loads(env.get('BUZZ_AUTH_TAG', 'null'))
        if (not isinstance(auth, list) or len(auth) != 4 or not all(isinstance(x, str) for x in auth)
                or auth[2] or env.get('BUZZ_ACP_AGENT_OWNER') != owner
                or authority.attested_owner({'tags': [auth]}, pub) != owner):
            raise ValueError
        channels = tuple(join._allowlist(env.get('BUZZ_ACP_CHANNELS', '')))
        if len(set(channels)) != len(channels): raise ValueError
        # No allowlist is valid for a locally configured, as-yet-unbound agent;
        # it does not authorize a channel. The resolver must verify membership.
        prompt = _path(env['BUZZ_ACP_SYSTEM_PROMPT_FILE']) if env.get('BUZZ_ACP_SYSTEM_PROMPT_FILE') else None
        responsible = _path(env['BUZZ_RESPONSIBLE_CONFIG']) if env.get('BUZZ_RESPONSIBLE_CONFIG') else None
    except Exception:
        reasons.append('agent_identity_invalid')
    if prompt is None or responsible is None:
        reasons.append('runtime_paths_missing')
    else:
        try:
            _runtime_file(prompt); _runtime_file(responsible)
        except (OSError, ValueError): reasons.append('runtime_paths_invalid')
    if block:
        try:
            profile_raw = read_owned(cfg / 'config.json'); profile_hash = _digest(profile_raw)
            profile = json.loads(profile_raw); apps = profile.get('apps') if isinstance(profile, dict) else None
            if not isinstance(apps, list) or any(not isinstance(x, dict) for x in apps): raise ValueError
            matches = [x for x in apps if x.get('appId') == app]
            if len(matches) != 1: raise ValueError
            selected = matches[0].get('name') or app
            if not isinstance(selected, str) or not selected or any(c.isspace() or ord(c) < 32 for c in selected): raise ValueError
            named = [x for x in apps if x.get('name') == selected]
            resolved = named if named else [x for x in apps if x.get('appId') == selected]
            if len(resolved) != 1 or resolved[0].get('appId') != app: raise ValueError
            # Decrypt only this app's actual pinned store; discard the secret.
            # No user token, global profile or another app is used as a fallback.
            from .secrets_store import app_secret
            app_secret(app, str(cfg), str(data))
            if read_owned(cfg / 'config.json') != profile_raw: raise ValueError
            bot_verified = True
        except (Exception, SystemExit): reasons.append('bot_profile_invalid')
    else:
        reasons.append('future_l6')
    if _identities(a) & legacy_ids: reasons.append('legacy_join_overlap')
    status = 'missing_bot' if block is None else ('blocked' if reasons else 'own_bot_verified')
    return AgentRecord(a['name'], env_file, a['unit'], owner, pub, channels, prompt, responsible,
                       _path(a['log_file']) if a.get('log_file') else None,
                       a['capabilities']['summary'], tuple(a['capabilities']['repos']), app, cfg, data,
                       env_hash, profile_hash, bot_verified, status, tuple(reasons))


def load(catalog_path, *, legacy_join_path) -> Catalog:
    """Load exact protected files; legacy overlap always blocks runtime eligibility.

    Missing-bot rows remain disabled future-L6 records even when additional
    blockers exist. Callers must require status == own_bot_verified, then obtain
    fresh signed policy and membership proof before constructing enabled agents.
    """
    try:
        path, legacy_path = _path(str(catalog_path)), _path(str(legacy_join_path))
        raw, old_raw = read_owned(path), read_owned(legacy_path)
        doc, old = _document(raw, allow_empty=True), _document(old_raw, allow_empty=True)
        if doc['owner_pubkey'] != old['owner_pubkey']: raise ValueError
        legacy_ids = set().union(*(_identities(a) for a in old['agents'])) if old['agents'] else set()
        rows = [_record(a, doc['owner_pubkey'], legacy_ids) for a in doc['agents']]
        for i, row in enumerate(rows):
            collision = any(i != j and (_identities(doc['agents'][i]) & _identities(doc['agents'][j])
                                       or row.pubkey is not None and row.pubkey == other.pubkey)
                            for j, other in enumerate(rows))
            if collision:
                rows[i] = replace(row, status='missing_bot' if row.app_id is None else 'blocked',
                                  reasons=tuple(dict.fromkeys((*row.reasons, 'catalog_identity_overlap'))))
        # Don't return exclusion proof assembled across different config revisions.
        if read_owned(path) != raw or read_owned(legacy_path) != old_raw: raise ValueError
        return Catalog(tuple(rows), path, legacy_path, _digest(raw), _digest(old_raw))
    except (Exception, SystemExit):
        raise CatalogError(NOTICE) from None
