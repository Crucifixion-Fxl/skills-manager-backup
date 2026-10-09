"""Read-only native subscription proof bound to the selected actual process.

The canonical launcher may supply the recovery directory in its initial process
environment. It is not inferred from a chat, app, home path or log filename.
No signal, restart, recovery task or journal mutation belongs in this reader.
"""
from pathlib import Path
import re

import buzz_feishu_group_sync as gs
from agent_recovery import _validate
from recovery_controller import load_snapshots, process_live, read_env
import recovery_inventory
from recovery_origin import canonical_origin
from recovery_runtime import verify_process


def inspect(spec, identity, *, phases=frozenset({'ready'})):
    pid, start, invocation, actual = identity
    desired = read_env(spec.env_file)
    for env in (desired, actual):
        if (gs._signer_pubkey(gs.secret_hex(env.get('BUZZ_PRIVATE_KEY'), 'agent')) != spec.pubkey
                or env.get('BUZZ_ACP_AGENT_OWNER') != spec.owner_pubkey
                or env.get('BUZZ_ACP_SYSTEM_PROMPT_FILE') != spec.prompt_file
                or env.get('BUZZ_RESPONSIBLE_CONFIG') != spec.responsible_file):
            raise ValueError('native identity mismatch')
    for name, pattern in (('BUZZ_ACP_BINARY_SHA256', '[0-9a-f]{64}'),
                          ('BUZZ_ACP_RECOVERY_REVISION', '[0-9a-f]{40}')):
        if (not isinstance(desired.get(name), str) or not re.fullmatch(pattern, desired[name])
                or desired[name] != actual.get(name)):
            raise ValueError('native release mismatch')
    relay = canonical_origin(desired['BUZZ_RELAY_URL'])
    if canonical_origin(actual['BUZZ_RELAY_URL']) != relay:
        raise ValueError('native relay mismatch')
    directory = actual.get('BUZZ_ACP_RECOVERY_DIR')
    if (not isinstance(directory, str) or not Path(directory).is_absolute()
            or desired.get('BUZZ_ACP_RECOVERY_DIR', directory) != directory):
        raise ValueError('native journal path mismatch')
    # Use the same strict channel parser as approved onboarding configuration.
    import buzz_agent_join_requests as joins
    from .bot_admission import channel_mode
    all_member = channel_mode(desired, '') == 'all_member'
    if (all_member != (channel_mode(actual, '') == 'all_member')
            or (all_member and desired['BUZZ_ACP_SUBSCRIBE'] != actual['BUZZ_ACP_SUBSCRIBE'])):
        raise ValueError('native subscription mode changed')
    channels = joins._allowlist(desired.get('BUZZ_ACP_CHANNELS', ''))
    if (len(channels)>4096 or len(channels)!=len(set(channels))
            or channels != joins._allowlist(actual.get('BUZZ_ACP_CHANNELS', ''))):
        raise ValueError('native subscriptions changed')
    snapshots = load_snapshots(directory)
    for snapshot in snapshots:
        _validate(snapshot, spec.pubkey, relay)
        recovery_inventory.validate(snapshot)
    live = [snapshot for snapshot in snapshots if process_live(snapshot)]
    if len(live) != 1:
        raise ValueError('native live generation ambiguous')
    current = live[0]
    proof = dict(snapshot=current, main_pid=pid,
                 binary_sha256=desired['BUZZ_ACP_BINARY_SHA256'],
                 revision=desired['BUZZ_ACP_RECOVERY_REVISION'])
    if (current['phase'] not in phases or current['runtime_policy']['owner'] != spec.owner_pubkey
            or current['process_start_ticks'] != str(start) or not verify_process(**proof)):
        raise ValueError('native process not ready')
    recovery_inventory.pending(snapshots, current['generation'])
    observed = current.get('channels')
    if (not isinstance(observed, list) or len(observed) != len(set(observed))
            or any(not isinstance(value, str) or not gs.UUID_RE.fullmatch(value) for value in observed)
            or len(observed) > 4096
            or (not all_member and (not set(observed) <= set(channels)
                or (current['phase'] == 'ready' and set(observed) != set(channels))))):
        raise ValueError('native subscriptions unverified')
    if read_env(spec.env_file) != desired or not verify_process(**proof):
        raise ValueError('native proof changed')
    return current, proof


def subscribed(spec, channel, identity):
    try:
        desired=read_env(spec.env_file)
        if not any('BUZZ_ACP_RECOVERY_REVISION' in env for env in (desired,identity[3])):
            return None  # Legacy processes retain their exact journal proof.
        # Native lazy-pool sleep calls not_ready(): phase becomes starting,
        # while this same live process retains the channels recorded only by
        # ready() after successful subscriptions. Onboarding proves subscribed
        # configuration, not an already-warm model pool. A fresh starting
        # generation has no channels and cannot pass this check.
        snapshot, _proof = inspect(spec, identity, phases={'ready', 'starting'})
        import buzz_agent_join_requests as joins
        expected = joins._allowlist(desired.get('BUZZ_ACP_CHANNELS', ''))
        from .bot_admission import channel_mode
        return (channel in snapshot['channels']
                and (channel_mode(desired, channel) == 'all_member'
                     or set(snapshot['channels']) == set(expected)))
    except Exception:
        return False
