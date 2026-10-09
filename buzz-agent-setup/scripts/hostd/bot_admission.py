"""Read-only local bot authorization; public membership never creates a grant."""
import json
from pathlib import Path

import buzz_feishu_group_sync as gs
import buzz_agent_join_requests as join
import recovery_authority as authority
try:
    from .bot_clients import BotLarkCli, _trusted_relay
    from .safety import read_owned
    from .config import load_config
except ImportError:
    from bot_clients import BotLarkCli, _trusted_relay
    from safety import read_owned
    from config import load_config


def channel_mode(env, channel):
    """Recognize explicit harness modes; missing/empty configuration is not a grant."""
    if 'BUZZ_ACP_CHANNELS' in env:
        return 'finite' if channel in join._allowlist(env['BUZZ_ACP_CHANNELS']) else None
    if env.get('BUZZ_ACP_SUBSCRIBE') in ('mentions', 'all'):
        return 'all_member'
    return None


def all_member_proof(run, pub, owner, query, now):
    """Fresh relay-signed membership within separately proved local channel authority.

    Global channel_add_policy governs new membership, not delivery on an already
    authorized channel. Its event may be absent and is not a local grant source.
    """
    try:
        from .claim_sync_app import roster
    except ImportError:
        from claim_sync_app import roster
    pin = getattr(run, 'claim_relay_pubkey', '')
    if not isinstance(pin, str) or not gs.HEX64_RE.fullmatch(pin):
        raise ValueError('missing relay membership authority')
    rows = query([{'kinds': [39002], 'authors': [pin], '#d': [run.cfg['channel_id']], 'limit': 257}])
    roster(rows, pin, run.cfg['channel_id'], pub, run.cfg['mirror_pubkey'], owner, now)


def channel_candidate(binding, spec, store_path, owner, trusted_relays):
    """Scheduling only: all-member candidates still need exact local provenance."""
    try:
        from types import SimpleNamespace
        try:
            from .store import Store
        except ImportError:
            from store import Store
        env = join.parse_env(read_owned(spec.env_file).decode())
        mode = channel_mode(env, binding.channel_id)
        if mode != 'all_member':
            return mode == 'finite'
        cfg = load_config(binding.config)
        with Store(store_path) as db:
            run = SimpleNamespace(cfg=cfg, binding_id=binding.name, mapping_store=db,
                clients=SimpleNamespace(relay_url=binding.relay_url),
                _signer=lambda: gs.load_signer_key(Path(cfg['people_api']['signer_env_file'])))
            return local_authorization(run, spec.pubkey, spec.app_id, env_file=spec.env_file,
                owner=owner, config_dir=getattr(spec, 'lark_config_dir', None) or getattr(spec, 'reader_config_dir', None),
                data_dir=getattr(spec, 'lark_data_dir', None) or getattr(spec, 'reader_data_dir', None),
                trusted_relays=trusted_relays, outlet=True) is not None
    except Exception:
        return False


def local_authorization(run, pub, app, *, env_file, config_dir, data_dir,
                        owner, trusted_relays=(), outlet=False, config_path=None, legacy_only=False):
    """Return protected config for one authorized effect, otherwise fail closed.

    A currently approved JOIN can start its outlet before final readback creates
    an active grant. Membership repair/intro require that completed grant. The
    legacy route is explicit protected config + own env, never an observed row.
    """
    try:
        db = run.mapping_store.conn
        binding = db.execute('SELECT * FROM binding WHERE binding_id=?', (run.binding_id,)).fetchone()
        if binding is None or binding['status'] in ('paused', 'retired', 'conflict'):
            return None
        path = Path(binding['config_path'])
        if config_path is not None and Path(config_path) != path:
            return None
        cfg = load_config(path)
        if (any(cfg[k] != run.cfg[k] for k in ('channel_id', 'chat_id', 'mirror_pubkey', 'desk_pubkey', 'people_api'))
                or binding['channel_id'] != cfg['channel_id'] or binding['chat_id'] != cfg['chat_id']
                or binding['mirror_pubkey'] != cfg['mirror_pubkey']
                or binding['sync_app_id'] != cfg['agents'][cfg['desk_pubkey']]['app_id']
                or binding['chat_ref'] not in ('', gs.chat_ref(cfg['chat_id']))):
            return None
        current_owner = gs._signer_pubkey(gs.load_signer_key(Path(cfg['people_api']['signer_env_file'])))
        if current_owner != owner or gs._signer_pubkey(run._signer()) != owner:
            return None
        env = join.parse_env(read_owned(env_file).decode())
        if (gs._signer_pubkey(gs.secret_hex(env.get('BUZZ_PRIVATE_KEY', ''), 'own agent')) != pub
                or env.get('BUZZ_ACP_AGENT_OWNER') != owner
                or channel_mode(env, cfg['channel_id']) is None
                or _trusted_relay(env.get('BUZZ_RELAY_URL', ''), trusted_relays) != run.clients.relay_url):
            return None
        auth = json.loads(env.get('BUZZ_AUTH_TAG', 'null'))
        if (not isinstance(auth, list) or len(auth) != 4 or auth[2]
                or authority.attested_owner({'tags': [auth]}, pub) != owner):
            return None
        if not config_dir or not data_dir or BotLarkCli(app, config_dir, data_dir, base_env={}).identity() != (app, ''):
            return None
        agent = db.execute('SELECT * FROM agent WHERE pubkey=?', (pub,)).fetchone()
        if agent is not None and (agent['status'] != 'active' or agent['owner_pubkey'] != owner or agent['app_id'] != app):
            return None
        grant = db.execute('SELECT * FROM agent_chat WHERE agent_id=? AND chat_id=?', (pub, cfg['chat_id'])).fetchone()
        if grant is not None and (agent is None or grant['status'] != 'active'
                or grant['binding_id'] != run.binding_id or grant['chat_ref'] != gs.chat_ref(cfg['chat_id'])):
            return None
        requests = db.execute('SELECT * FROM join_request WHERE agent_id=? AND chat_id=?', (pub, cfg['chat_id'])).fetchall()
        if requests:
            for request in requests:
                if (request['status'] not in ('approved', 'applied', 'done')
                        or request['owner_pubkey'] != owner or request['callback_app_id'] != app
                        or run.mapping_store.card_approval_decision(request['request_id']) is None):
                    continue
                plan = run.mapping_store.effect_plan(request['request_id'])
                if (not plan or plan['binding_id'] != run.binding_id or plan['channel_id'] != cfg['channel_id']
                        or Path(plan['config_path']) != path):
                    continue
                if request['kind'] == 'channel' and request['binding_id'] != run.binding_id:
                    continue
                if request['kind'] == 'new_binding' and (plan['mirror_pubkey'] != cfg['mirror_pubkey'] or binding['sync_app_id'] != app):
                    continue
                if (not legacy_only and agent is not None and ((grant is not None and request['status'] == 'done')
                        or (outlet and channel_mode(env, cfg['channel_id']) == 'finite'
                            and request['status'] in ('approved', 'applied')))):
                    return cfg
        # Reconnect can create a redundant requested JOIN for an old explicit
        # configuration. That observation does not revoke its legacy authority.
        # A workflow-generated new binding config is not a legacy baseline.
        generated = db.execute("""SELECT 1 FROM effect_plan p JOIN join_request j ON j.request_id=p.request_id
            WHERE j.kind='new_binding' AND p.config_path=? LIMIT 1""", (str(path),)).fetchone()
        if generated:
            return None
        configured = cfg['agents'].get(pub)
        if (configured is not None and configured['app_id'] == app
                and Path(configured['lark_config_dir']) == Path(config_dir)
                and Path(configured['lark_data_dir']) == Path(data_dir)):
            return cfg
        return None  # An orphan active grant can be an old outlet observation.
    except Exception:
        return None
