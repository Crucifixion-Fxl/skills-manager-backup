"""Live Buzz events for one binding: a NIP-42-authenticated WebSocket to the relay as the binding's mirror agent.

Subscribes to the channel (`#h`) for the kinds the sync mirrors; each new event marks the binding dirty for the
Buzz -> Feishu phase (P0-12: a single `#h` subscription is pushed in ~20 ms). The mirror's own events are ignored
here (they are the Feishu -> Buzz echo). Reconnects with backoff and asks for a catch-up run after each reconnect.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import inspect
import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable, Collection
from urllib.parse import urlsplit

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs  # noqa: E402
from issue_thread_router import BUZZ_ALLOWED_RELAY_HOSTNAME_SHA256  # noqa: E402

try:
    from .safety import read_owned, notice, SafetyError
    from .http_pool import PoolError, valid_http_diagnostic
except ImportError:
    from safety import read_owned, notice, SafetyError
    from http_pool import PoolError, valid_http_diagnostic

# The host entry point imports its pool via the package and this feed by its
# legacy module name. Accept only these two trusted concrete class identities.
from hostd.http_pool import PoolError as _CanonicalPoolError

KINDS = [9, 7, 5, 40003, 9000, 9001]
MAX_CONTENT_BYTES = 2 ** 20
MAX_TAGS = 4096
MAX_TAG_ITEMS = 64
MAX_TAG_ITEM_BYTES = 8192
MAX_TAG_BYTES = 2 ** 18
STABLE_CONNECTION_SECONDS = 60


def _env(path: str) -> dict[str, str]:
    out = {}
    for line in read_owned(path).decode().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _origin(relay: str) -> tuple[str, str, int | None, bool]:
    if (not isinstance(relay, str) or not relay.isascii() or not relay
            or any(c.isspace() for c in relay) or any(c in relay for c in "?#\\")):
        raise SafetyError(notice("relay_config"))
    try:
        parsed = urlsplit(relay)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise SafetyError(notice("relay_config")) from None
    if (not host or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.scheme not in ("https", "wss", "http", "ws")):
        raise SafetyError(notice("relay_config"))
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    expected_host = f"[{host}]" if ":" in host else host
    if parsed.netloc != expected_host + (f":{port}" if port is not None else ""):
        raise SafetyError(notice("relay_config"))
    scheme = "wss" if parsed.scheme in ("https", "wss") else "ws"
    if scheme == "ws" and not loopback:
        raise SafetyError(notice("relay_config"))
    return scheme, host, port, loopback


def _ws_url(relay: str, trusted_relays: Collection[str] = ()) -> str:
    """Exact approved origin before connect/sign; never infer trust from mirror.env.

    The default host digest comes from the existing issue-thread router policy.
    Additional origins must be explicitly supplied by owner-controlled config.
    Plain transport is restricted to loopback, including explicitly allowed origins.
    """
    scheme, host, port, loopback = _origin(relay)
    if isinstance(trusted_relays, str) or not isinstance(trusted_relays, (tuple, list, set, frozenset)):
        raise SafetyError(notice("relay_config"))
    allowed = {_origin(value)[:3] for value in trusted_relays}
    approved = (scheme == "wss" and port is None
                and hashlib.sha256(host.encode()).hexdigest() == BUZZ_ALLOWED_RELAY_HOSTNAME_SHA256)
    if not (approved or loopback or (scheme, host, port) in allowed):
        raise SafetyError(notice("relay_config"))
    netloc = f"[{host}]" if ":" in host else host
    return f"{scheme}://{netloc}" + (f":{port}" if port is not None else "")


def _frame(raw) -> list | None:
    try:
        if not isinstance(raw, (str, bytes)) or len(raw) > 2 ** 22:
            return None
        msg = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(msg, list) or not msg or not isinstance(msg[0], str):
        return None
    kind = msg[0]
    if kind == "AUTH":
        valid = len(msg) == 2 and isinstance(msg[1], str) and 0 < len(msg[1]) <= 4096
    elif kind == "OK":
        valid = (len(msg) == 4 and isinstance(msg[1], str)
                 and type(msg[2]) is bool and isinstance(msg[3], str))
    elif kind == "EVENT":
        valid = len(msg) == 3 and isinstance(msg[1], str) and isinstance(msg[2], dict)
    elif kind == "CLOSED":
        valid = len(msg) == 3 and isinstance(msg[1], str) and isinstance(msg[2], str)
    elif kind in ("EOSE", "NOTICE"):
        valid = len(msg) == 2 and isinstance(msg[1], str)
    else:
        valid = False
    return msg if valid else None


def _connect(url: str):
    """The handshake must retain the exact approved origin before AUTH can be sent.

    websockets 15 follows HTTP redirects by default; its process_redirect hook runs
    before opening the redirected socket, rather than after credentials are sent.
    """
    connector = websockets.connect(url, open_timeout=20, max_size=2 ** 22, ping_interval=30)
    process_redirect = connector.process_redirect

    def guarded_redirect(exc: Exception):
        target = process_redirect(exc)
        if isinstance(target, str):
            try:
                if _ws_url(target, (url,)) != url:
                    return SafetyError(notice("relay_config"))
            except Exception:
                return SafetyError(notice("relay_config"))
        return target

    connector.process_redirect = guarded_redirect
    return connector


def _target_hint(event):
    # The relay resolves #h through targets for reactions and deletions.
    # This wakes a fresh read; the worker still proves author and saved receipt
    # ownership before any effect. An explicit h never takes this exception.
    tags = event['tags']
    references = [tag for tag in tags if tag and tag[0] == 'e']
    return (event['kind'] in (5, 7) and not any(tag and tag[0] == 'h' for tag in tags)
            and len(references) == 1 and len(references[0]) >= 2
            and gs.HEX64_RE.fullmatch(references[0][1]) is not None)


def _event(value: dict, channel_id: str) -> dict | None:
    fields = ("id", "pubkey", "created_at", "kind", "tags", "content", "sig")
    if not isinstance(value, dict) or not all(field in value for field in fields):
        return None
    # Extensions from the wire cannot masquerade as local `_reconnected` controls.
    ev = {field: value[field] for field in fields}
    if (not isinstance(ev["id"], str) or not gs.HEX64_RE.fullmatch(ev["id"])
            or not isinstance(ev["pubkey"], str) or not gs.HEX64_RE.fullmatch(ev["pubkey"])
            or not isinstance(ev["sig"], str) or re.fullmatch(r"[0-9a-f]{128}", ev["sig"]) is None
            or not isinstance(ev["content"], str) or len(ev["content"]) > MAX_CONTENT_BYTES
            or type(ev["kind"]) is not int or ev["kind"] not in KINDS
            or type(ev["created_at"]) is not int or not 0 <= ev["created_at"] < 2 ** 63
            or not isinstance(ev["tags"], list)
            or len(ev["tags"]) > MAX_TAGS
            or not all(isinstance(tag, list) and len(tag) <= MAX_TAG_ITEMS
                       and all(isinstance(item, str) and len(item) <= MAX_TAG_ITEM_BYTES for item in tag)
                       for tag in ev["tags"])
            or (["h", channel_id] not in ev["tags"] and not _target_hint(ev))):
        return None
    try:
        if (len(ev["content"].encode()) > MAX_CONTENT_BYTES
                or sum(len(item.encode()) for tag in ev["tags"] for item in tag) > MAX_TAG_BYTES
                or any(len(item.encode()) > MAX_TAG_ITEM_BYTES for tag in ev["tags"] for item in tag)
                or not gs._nip01_event_verified(ev)):
            return None
    except Exception:
        return None  # Untrusted input and validator failures cannot tear down this subscription.
    return ev


# Fixed metadata classifications and trusted source locations only.
_FAILURE_STAGES = frozenset({'config','connect','replay','auth_wait','auth_sign','auth_send',
    'subscribe','catch_up','event_wait','event_callback','round_outlets','round_dispatch',
    'round_result','round_status','phase_buzz','phase_feishu','phase_members','own_outlet'})
_SOURCE_FUNCTIONS = frozenset({'follow','_connect','guarded_redirect','_env','_ws_url','_origin',
    '_round','_run','_run_outlets','worker','replay_since','outlet_since','set_status','save_status','on_relay',
    'refresh_outlets','__init__','_check_files','_directory','transaction','record_connection',
    'has_replay_state','cursor_position','run','_reader_state','read_owned','_read_owned',
    'sign_event','_signer_pubkey','secret_hex','schnorr_sign','schnorr_verify','point_mul',
    '_call','_run','request','_http_get','_binding_state','mark','retain_notice_hint',
    'verify_identities','load_people','whoami','identity','bot_scopes','_http_request','_http_call',
    '_scope','_token','_acquire','_exchange','_exchange_transport','parse_bot_scope_envelope','_need',
    'feishu_to_buzz','mirror','_recover_feishu_mapping','resolve_feishu','recover_feishu_event',
    'root_for_event','_mirror_publish','_adopt','_delivery_settled','reserve_delivery',
    'ack_delivery','save','load','_save','_validated',
    'catch_up','verify','_query','deliver','_deliver','_dependency_target','_target',
    '_reaction','_retained_reaction_scope','_only_orphan_withdrawals','_latest_edit',
    'signed_history','read_history','page','fetch','buzz_to_feishu','_prepare_outbound',
    '_thread_root_parent','resolve_buzz','_recover_buzz_mapping','_recover_buzz_target'})
_SOURCE_FILES = set()
# Both module spelling and resolved release spelling are trusted; the installer
# may launch through its current-release symlink without changing Source bytes.
for _source_root in {Path(__file__).parent, Path(__file__).resolve().parent}:
    _SOURCE_FILES.update(_source_root/name for name in
        ('relay_feed.py','__main__.py','store.py','binding_worker.py','safety.py','bot_clients.py',
         'bot_round.py','http_pool.py','native_scopes.py','delivery_mapping.py','state_store.py',
         'outlet.py','relay_history.py'))
    _SOURCE_FILES.add(_source_root.parent/'buzz_feishu_group_sync.py')
    _SOURCE_FILES.add(_source_root.parent.parent/'references/scripts/nostrkit.py')


def failure_diagnostic(stage: str, error: Exception) -> dict:
    import sqlite3
    classes = (('CliError',gs.CliError),('GroupSyncError',gs.GroupSyncError),
        ('TimeoutError',TimeoutError),('SQLiteError',sqlite3.Error),
        ('OSError',OSError),('ValueError',ValueError),('TypeError',TypeError),
        ('RuntimeError',RuntimeError),('KeyError',KeyError))
    label = next((name for name,kind in classes if isinstance(error,kind)), 'OtherError')
    location=[];frame=error.__traceback__
    for _ in range(64):
        if frame is None:
            break
        code=frame.tb_frame.f_code
        if Path(code.co_filename) in _SOURCE_FILES and code.co_name in _SOURCE_FUNCTIONS:
            location.append({'file':Path(code.co_filename).name,'function':code.co_name,'line':frame.tb_lineno})
        frame=frame.tb_next
    result = {'stage':stage if stage in _FAILURE_STAGES else 'config',
              'error_type':label,'location':location[-6:]}
    # Do not duck-type arbitrary exceptions or follow their cause/context chain.
    if type(error) in (PoolError, _CanonicalPoolError):
        metadata = error.__dict__.get('http_diagnostic')
        if valid_http_diagnostic(metadata):
            result['http'] = dict(metadata)
    return result


def valid_diagnostic(value) -> bool:
    return (type(value) is dict and set(value) in ({'stage','error_type','location'},
                                                {'stage','error_type','location','http'})
        and ('http' not in value or valid_http_diagnostic(value['http']))
        and type(value['stage']) is str and value['stage'] in _FAILURE_STAGES
        and type(value['error_type']) is str and value['error_type'] in {'CliError','GroupSyncError','TimeoutError','SQLiteError','OSError','ValueError','TypeError','RuntimeError','KeyError','OtherError'}
        and type(value['location']) is list and len(value['location'])<=6
        and all(type(row) is dict and set(row)=={'file','function','line'}
            and type(row['file']) is str and row['file'] in {p.name for p in _SOURCE_FILES}
            and type(row['function']) is str and row['function'] in _SOURCE_FUNCTIONS
            and type(row['line']) is int and 0<row['line']<100000 for row in value['location']))


def copy_diagnostic(value) -> dict:
    """Validated detached snapshot for status writers; reject unknown fields."""
    if not valid_diagnostic(value):
        raise ValueError('invalid failure diagnostic')
    result = {'stage':value['stage'], 'error_type':value['error_type'],
              'location':[dict(row) for row in value['location']]}
    if 'http' in value:
        result['http'] = dict(value['http'])
    return result


async def follow(name: str, mirror_env_file: str, channel_id: str,
                 on_event: Callable[[str, dict], Awaitable[None]], on_status: Callable[[str, str, str], None],
                 *, trusted_relays: Collection[str] = (),
                 replay_since: Callable[[], int] | None = None,
                 author: str | None = None, status_kind: str = 'relay',
                 on_failure: Callable[[str, str, dict], None] | None = None) -> None:
    async def status(value: str) -> None:
        try:
            result = on_status(name, status_kind, value)
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass  # persistence/UI failure must not escape this binding's feed

    def failure(stage, error):
        if on_failure is not None:
            try:
                on_failure(name, status_kind, failure_diagnostic(stage,error))
            except Exception:
                pass  # observation failure cannot replace the original outcome

    try:
        env = _env(mirror_env_file)
        url = _ws_url(env.get("BUZZ_RELAY_URL", ""), trusted_relays)
        key = gs.secret_hex(env.get("BUZZ_PRIVATE_KEY", ""), "mirror key")
        auth_tag = json.loads(env["BUZZ_AUTH_TAG"]) if env.get("BUZZ_AUTH_TAG") else None
        if auth_tag is not None and (not isinstance(auth_tag, list) or len(auth_tag) < 2
                                     or not all(isinstance(v, str) for v in auth_tag)):
            raise ValueError
        if status_kind not in {'relay', 'outlet'} or (author is None) != (status_kind == 'relay'):
            raise ValueError
        if author is not None:
            import recovery_authority as authority
            if (not isinstance(author, str) or not gs.HEX64_RE.fullmatch(author)
                    or gs._signer_pubkey(key) != author or not isinstance(auth_tag, list)
                    or len(auth_tag) != 4 or auth_tag[2]
                    or not authority.attested_owner({'tags': [auth_tag]}, author)):
                raise ValueError
    except Exception as error:
        failure("config",error)
        await status(notice("relay_config"))
        return
    backoff = 1.0
    while True:
        connected_at = None
        stage = 'replay'
        try:
            # Resolve the durable cursor before opening a socket. A contended
            # ledger lane must not spend the server's authentication window.
            now = int(time.time())
            since = max(0, now - gs.BUZZ_OVERLAP_SECONDS) if replay_since is None else replay_since()
            if inspect.isawaitable(since):
                since = await since
            if type(since) is not int or not 0 <= since <= int(time.time()):
                raise ValueError('invalid replay cursor')
            stage = 'connect'
            async with _connect(url) as ws:
                authed = False
                auth_id = None
                subscribed = False
                sub = "h" + secrets.token_hex(3)
                iterator = ws.__aiter__()
                auth_deadline = asyncio.get_running_loop().time() + 30
                try:
                    while True:
                        try:
                            stage = 'event_wait' if authed else 'auth_wait'
                            if authed:
                                raw = await iterator.__anext__()
                            else:
                                remaining = auth_deadline - asyncio.get_running_loop().time()
                                if remaining <= 0:
                                    raise asyncio.TimeoutError
                                async with asyncio.timeout(remaining):
                                    raw = await iterator.__anext__()
                        except StopAsyncIteration:
                            await status(notice("reconnect"))
                            break
                        msg = _frame(raw)
                        if msg is None:
                            await status(notice("frame"))
                            continue
                        if msg[0] == "AUTH":
                            if authed:
                                await status(notice("auth"))
                                break
                            tags = [["relay", url], ["challenge", msg[1]]]
                            if auth_tag:
                                tags.append(auth_tag)
                            stage = 'auth_sign'
                            signed = gs.sign_event(key, 22242, tags, "", int(time.time()))
                            auth_id = signed["id"]
                            stage = 'auth_send'
                            await ws.send(json.dumps(["AUTH", signed]))
                        elif msg[0] == "OK" and not authed and auth_id is not None and msg[1] == auth_id:
                            if msg[2] is not True:
                                await status(notice("auth"))
                                break
                            filt = {"#h": [channel_id], "kinds": KINDS if author is None else [9, 7, 5, 40003], "since": since}
                            if author is not None:
                                filt['authors'] = [author]
                            stage = 'subscribe'
                            await ws.send(json.dumps(["REQ", sub, filt]))
                            authed = subscribed = True
                            connected_at = time.monotonic()
                            await status("connected")
                            stage = 'catch_up'
                            await on_event(name, {"type": "_reconnected"})
                        elif msg[0] == "EVENT" and authed and msg[1] == sub:
                            event = _event(msg[2], channel_id)
                            if event is not None and (author is None or (
                                    event['pubkey'] == author and event['kind'] in (9, 7, 5, 40003)
                                    and (_target_hint(event) or [tag for tag in event['tags'] if tag and tag[0] == 'h'] == [['h', channel_id]]))):
                                stage = 'event_callback'
                                await on_event(name, event)
                            else:
                                await status(notice("frame"))
                        elif msg[0] == "CLOSED" and authed and msg[1] == sub:
                            await status(notice("closed"))
                            break
                finally:
                    if subscribed:
                        try:
                            await ws.send(json.dumps(["CLOSE", sub]))
                        except Exception:
                            pass  # socket may already be closed; context manager still releases it
        except Exception as error:  # adapter/callback failures stay inside this binding; cancellation propagates
            failure(stage,error)
            await status(notice("reconnect"))
        if connected_at is not None and time.monotonic() - connected_at >= STABLE_CONNECTION_SECONDS:
            backoff = 1.0
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)
