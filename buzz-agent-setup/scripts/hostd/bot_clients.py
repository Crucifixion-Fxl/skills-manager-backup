"""Bot-only CLI adapters for hostd; no personal login or global profile fallback.

Request shapes are checked against installed lark-cli help/embedded API schemas.
With a shared HttpPool, API operations use bot HTTP and per-request admission.
The exact /members/list endpoint includes bots and security truncations.
Without a pool the existing explicit-profile bot CLI adapter remains compatible.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import html
import ipaddress
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Collection
from urllib.parse import urlsplit, quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs
from issue_thread_router import BUZZ_ALLOWED_RELAY_HOSTNAME_SHA256
try:
    from .safety import read_owned
    from .scheduler import AdmissionError
    from .cli_runtime import RuntimePaths, RuntimePathError, default_paths, NOTICE as RUNTIME_NOTICE, NODE_ENV, ENTRY_ENV
except ImportError:
    from safety import read_owned
    from scheduler import AdmissionError
    from cli_runtime import RuntimePaths, RuntimePathError, default_paths, NOTICE as RUNTIME_NOTICE, NODE_ENV, ENTRY_ENV

@dataclass(frozen=True)
class MessagePage:
    rows: tuple[dict, ...]
    has_more: bool
    next_token: str


# Compatibility names for injected offline transports. Product calls resolve
# their startup overrides lazily, without owner- or version-specific literals.
_PROCESS_RUNTIME = default_paths()
NODE, CLI_ENTRY = _PROCESS_RUNTIME.node_binary, _PROCESS_RUNTIME.lark_cli_entry
_SUBPROCESS_RUN = subprocess.run
# Each group is N-of-1, not all listed scopes required. Official CLI API schemas
# describe alternatives; narrower scopes are preferred when granting permissions.
READ_SCOPE_GROUPS = {
    'im:chat.group_info:readonly': frozenset({'im:chat.group_info:readonly', 'im:chat:readonly', 'im:chat', 'im:chat:read'}),
    'im:chat.members:read': frozenset({'im:chat.members:read', 'im:chat.group_info:readonly', 'im:chat:readonly', 'im:chat'}),
    'im:message:readonly': frozenset({'im:message:readonly', 'im:message'}),
    'im:message.reactions:read': frozenset({'im:message.reactions:read', 'im:message:readonly', 'im:message'}),
}


def failure(reason: str, remedy: str) -> gs.GroupSyncError:
    return gs.GroupSyncError(f'{reason}。怎么解决：{remedy}。复制给 AI：帮我检查 hostd bot 身份、权限、完整分页与 union_id 映射，不要输出凭据或消息正文。')


class BotCliError(gs.CliError):
    """Keep retry/outcome fields while keeping arbitrary server text out of notices."""
    def __str__(self):
        if self.kind == 'cli_runtime':
            return RUNTIME_NOTICE
        return str(failure('bot API 操作失败', '检查本机应用权限与网络；结果未知时先核实投递账本再重试'))


def _trusted_relay(raw: str, trusted: Collection[str]) -> str:
    """Same approved-origin policy as relay_feed, without importing WebSocket dependencies."""
    def origin(value):
        if not isinstance(value, str) or not value.isascii() or any(c.isspace() for c in value) or any(c in value for c in '?#\\'):
            raise failure('relay 地址不安全', '使用已批准的安全 relay 或明确配置的本机测试地址')
        try:
            p = urlsplit(value)
            host, port = p.hostname, p.port
        except ValueError:
            raise failure('relay 地址不安全', '检查 relay 地址格式') from None
        if not host or p.username is not None or p.password is not None or p.path not in ('', '/') or p.scheme not in ('http', 'https', 'ws', 'wss'):
            raise failure('relay 地址不安全', '使用没有凭据和额外路径的 relay 地址')
        netloc = f'[{host}]' if ':' in host else host
        if p.netloc != netloc + (f':{port}' if port is not None else ''):
            raise failure('relay 地址不安全', '检查 relay 地址格式')
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == 'localhost'
        scheme = 'https' if p.scheme in ('https', 'wss') else 'http'
        if scheme == 'http' and not loopback:
            raise failure('relay 未使用受保护的连接', '为非本机 relay 使用 HTTPS')
        return scheme, host, port, loopback
    if isinstance(trusted, str) or not isinstance(trusted, (list, tuple, set, frozenset)):
        raise failure('可信 relay 配置无效', '使用明确的完整地址列表')
    scheme, host, port, loopback = origin(raw)
    approved = scheme == 'https' and port is None and hashlib.sha256(host.encode()).hexdigest() == BUZZ_ALLOWED_RELAY_HOSTNAME_SHA256
    if not approved and not loopback and (scheme, host, port) not in {origin(v)[:3] for v in trusted}:
        raise failure('relay 地址未获批准', '检查可信 relay 配置，不要从凭据文件自动授权地址')
    netloc = f'[{host}]' if ':' in host else host
    return f'{scheme}://{netloc}' + (f':{port}' if port is not None else '')


@dataclass(frozen=True)
class ReactionSnapshot:
    """Current selected-app reaction IDs; no human identities leave the reader."""
    message_id: str
    app_id: str
    emoji: str
    complete: bool
    reaction_ids: tuple[str, ...]


@dataclass(frozen=True)
class OwnImage:
    """Internal selected-app binary receipt; key/body never enter public status."""
    image_key: str = field(repr=False)
    mime_type: str
    byte_size: int
    content_hash: str
    body: bytes = field(repr=False)


@dataclass(frozen=True)
class BotMemberListing(gs.MemberListing):
    # Webhook/system bots have a real member ID but no application ID.
    # Preserve them as opaque occupied slots; never guess or remove them.
    opaque_bots: frozenset[str] = frozenset()


class BotLarkCli(gs.LarkCli):
    """Retain only the legacy adapter's valid bot sending operations.

    Every subprocess has a selected local app profile and explicit bot identity.
    Profile selection uses official CLI Config.FindApp semantics (name before
    appId); a colliding name is rejected, never silently selects another app.
    """
    def __init__(self, app_id: str, config_dir: str | Path, data_dir: str | Path,
                 *, base_env: Mapping[str, str], runner: Any = subprocess.run,
                 scheduler=None, chat_id=None, priority='normal', http_pool=None, runtime_paths=None):
        if not isinstance(app_id, str) or not gs.APP_ID_RE.fullmatch(app_id):
            raise failure('bot 应用配置无效', '检查应用 ID')
        self.app_id = app_id
        self.config_dir, self.data_dir = Path(config_dir), Path(data_dir)
        self.active_phase = ''
        self.partial_reads: set[str] = set()
        self.scheduler, self.chat_id, self.priority = scheduler, chat_id, priority
        self.http_pool = http_pool
        if runtime_paths is not None and not isinstance(runtime_paths, RuntimePaths):
            raise failure('CLI 运行路径配置无效', '使用显式的 RuntimePaths 配置')
        self.runtime_paths = runtime_paths
        self._runtime_env = {key: base_env[key] for key in ('HOME', 'PATH', 'NPM_CONFIG_PREFIX', 'npm_config_prefix', NODE_ENV, ENTRY_ENV) if key in base_env}
        self._explicit_runtime = any(key in base_env or key in os.environ for key in (NODE_ENV, ENTRY_ENV))
        if not self.config_dir.is_absolute() or not self.data_dir.is_absolute():
            raise failure('bot 凭据目录不明确', '配置本机应用的绝对目录，不能使用个人全局 profile')
        env = gs.child_env(base_env, {**gs.LARK_ENV, 'LARKSUITE_CLI_CONFIG_DIR': str(self.config_dir),
                                   'LARKSUITE_CLI_DATA_DIR': str(self.data_dir), 'LARKSUITE_CLI_NO_SKILLS_NOTIFIER': '1'})
        super().__init__(NODE, env, runner=runner)

    def _profile(self) -> str:
        try:
            raw = json.loads(read_owned(self.config_dir / 'config.json'))
        except (ValueError, OSError):
            raise failure('bot profile 无法安全读取', '检查本机应用配置文件的所有者与 0600 权限') from None
        apps = raw.get('apps') if isinstance(raw, dict) else None
        if not isinstance(apps, list) or any(not isinstance(app, dict) for app in apps):
            raise failure('bot profile 格式无效', '检查本机应用配置的 apps 列表')
        matches = [app for app in apps if app.get('appId') == self.app_id]
        if len(matches) != 1:
            raise failure('bot profile 与应用不一致', '将本机应用绑定到正确的凭据目录')
        profile = matches[0].get('name') or self.app_id
        if not isinstance(profile, str) or not profile or any(c.isspace() or ord(c) < 32 for c in profile):
            raise failure('bot profile 名称无效', '检查本机 profile 名称')
        named = [app for app in apps if app.get('name') == profile]
        resolved = named if named else [app for app in apps if app.get('appId') == profile]
        if len(resolved) != 1 or resolved[0].get('appId') != self.app_id:
            raise failure('bot profile 名称与应用冲突', '修复重复 profile 名称，不要切换到其他应用')
        return profile

    def identity(self) -> tuple[str, str]:
        self._profile()
        return self.app_id, ''  # No user's token or app-scoped human open_id is consulted.

    def _run(self, what: str, args: list[str], cwd: Path | None = None, *, private_files=False,
             raw_image_envelope=False):
        args = list(args)
        if raw_image_envelope:
            prefix = '/open-apis/im/v1/images/'
            encoded = args[2][len(prefix):] if len(args) > 2 and args[2].startswith(prefix) else ''
            requested = unquote(encoded)
            if (what != 'own image readback' or len(args) != 7
                    or args[:2] != ['api', 'GET'] or not self._image_key(requested)
                    or args[2] != prefix + quote(requested, safe='')
                    or args[3:5] != ['--output', 'image.bin'] or args[5:] != ['--as', 'bot']):
                raise failure('二进制图片读取请求无效', '只读取已固定的原图片标识')
        if any(a.startswith('--as=') or a.startswith('--profile=') for a in args) or '--profile' in args:
            raise failure('请求试图改用其他身份', '保持 hostd 选定的 bot profile')
        indices = [i for i, arg in enumerate(args) if arg == '--as']
        if len(indices) > 1 or (indices and (indices[0] + 1 >= len(args) or args[indices[0] + 1] != 'bot')):
            raise failure('hostd 不能使用个人身份', '将操作迁移为 bot 支持的 API')
        if not indices:
            args += ['--as', 'bot']
        args += ['--profile', self._profile()]
        extra = {} if cwd is None else {'cwd': str(cwd)}
        if private_files:
            # Child-only file creation policy; never change this process's
            # umask while unrelated async/thread operations may run.
            extra['umask'] = 0o077
        try:
            # Injected offline runners without explicit paths retain the
            # discoverable process constants used by legacy transport fixtures.
            # Such trusted test injections must never launch a real CLI.
            paths = self.runtime_paths or (RuntimePaths.resolve(self._runtime_env)
                if self.runner is _SUBPROCESS_RUN or self._explicit_runtime else _PROCESS_RUNTIME)
            if self.runner is _SUBPROCESS_RUN or self._explicit_runtime or self.runtime_paths is not None:
                paths.validate()
        except RuntimePathError:
            raise BotCliError(what, -1, 'cli_runtime', definite=True, error_type='validation') from None
        try:
            def dispatch():
                return self.runner([paths.node_binary, paths.lark_cli_entry, *args], capture_output=True, text=True, timeout=90,
                                   check=False, env=self.env, **extra)
            result = (self.scheduler.run(dispatch, app_id=self.app_id, chat_id=self.chat_id,
                       priority=self.priority, timeout=90) if self.scheduler is not None else dispatch())
        except AdmissionError:
            # No request was sent. A cancelled/expired queue entry is a
            # definite refusal, never an ambiguous delivery outcome.
            raise BotCliError(what, -1, 'rate_limited', definite=True) from None
        except (OSError, subprocess.TimeoutExpired):
            raise BotCliError(what, -1, 'network', definite=False) from None
        return result, None if raw_image_envelope else gs._parse_output(result)

    def call(self, what: str, args: list[str], *, full=False, cwd=None, private_files=False):
        if self.http_pool is not None:
            payload = self._http_call(what, list(args), cwd)
            if payload is not None:
                return payload if full else payload['data']
        result, payload = (self._run(what, [*args, '--format', 'json'], cwd, private_files=True)
                           if private_files else self._run(what, [*args, '--format', 'json'], cwd))
        if not isinstance(payload, dict):
            raise failure('CLI 未确认 bot 身份', '检查 CLI 的 bot profile 与输出契约')
        if result.returncode != 0 or payload.get('ok') is not True:
            error = payload.get('error') if isinstance(payload.get('error'), dict) else {}
            kind = error.get('subtype')
            kind = kind if isinstance(kind, str) and kind in ('not_found', 'authentication', 'token_missing', 'permission', 'rate_limited', 'network') else ''
            error_type = error.get('type')
            error_type = error_type if isinstance(error_type, str) and error_type in gs.LARK_DEFINITE_ERROR_TYPES | {'network'} else ''
            raise BotCliError(what, error.get('code') if type(error.get('code')) is int else result.returncode or -1,
                              kind, definite=error_type in gs.LARK_DEFINITE_ERROR_TYPES, error_type=error_type)
        if payload.get('identity') != 'bot':
            raise failure('CLI 未确认 bot 身份', '检查 CLI 的 bot profile 与输出契约')
        if full:
            return payload
        if not isinstance(payload.get('data'), dict):
            raise failure('bot API 应答格式无效', '检查 CLI 与 API 的兼容性')
        return payload['data']

    @staticmethod
    def _flags(args, allowed):
        values = {}
        switches = {'--page-all', '--no-reactions', '--reply-in-thread'}
        index = 0
        while index < len(args):
            flag = args[index]
            if flag not in allowed or flag in values:
                raise failure('HTTP 请求参数不受支持', '使用已支持的 bot API 参数，不能静默切换 CLI')
            if flag in switches:
                values[flag] = True; index += 1
            else:
                if index + 1 >= len(args):
                    raise failure('HTTP 请求参数不完整', '检查 bot API 参数')
                values[flag] = args[index + 1]; index += 2
        return values

    def _http_request(self, method, path, params=None, data=None):
        # Profile validation is repeated for every real request, including pages.
        self._profile()
        chat = self.chat_id
        # Known targets determine their actual lane without shared mutation.
        # Message-only calls retain the caller's verified binding/chat context.
        match = re.fullmatch(r'/open-apis/im/v1/chats/(oc_[A-Za-z0-9_]+)(?:/.*)?', path)
        if match: chat = match[1]
        if isinstance(params, dict):
            if params.get('container_id_type') == 'chat': chat = params.get('container_id')
            if params.get('receive_id_type') == 'chat_id' and isinstance(data, dict): chat = data.get('receive_id')
        if chat is not None and (not isinstance(chat, str) or not gs.CHAT_ID_RE.fullmatch(chat)):
            raise failure('HTTP 群目标无效', '检查本次请求的实际群绑定')
        return self.http_pool.request(self.app_id, self.config_dir, self.data_dir, method, path,
                                      params=params, data=data, chat_id=chat, priority=self.priority)

    def _http_call(self, what, args, cwd):
        # Keep the same identity guard as subprocess mode before any transport.
        if any(a.startswith('--as=') or a.startswith('--profile=') for a in args) or '--profile' in args:
            raise failure('请求试图改用其他身份', '保持 hostd 选定的 bot profile')
        indices = [i for i, arg in enumerate(args) if arg == '--as']
        if len(indices) > 1 or (indices and (indices[0] + 1 >= len(args) or args[indices[0] + 1] != 'bot')):
            raise failure('hostd 不能使用个人身份', '将操作迁移为 bot 支持的 API')
        if indices: del args[indices[0]:indices[0] + 2]
        self._profile()
        # Explicit media exceptions only. These retain exact bot/profile CLI.
        if args[:3] == ['im', 'images', 'create'] or args[:2] == ['im', '+messages-resources-download'] or (args[:2] in
                (['im', '+messages-send'], ['im', '+messages-reply']) and '--image' in args):
            return None
        try:
            if args[:1] == ['api'] and len(args) >= 3:
                flags = self._flags(args[3:], {'--params', '--data'})
                return self._http_request(args[1], args[2],
                    json.loads(flags['--params']) if '--params' in flags else None,
                    json.loads(flags['--data']) if '--data' in flags else None)
            if args[:2] in (['im', '+messages-send'], ['im', '+messages-reply']):
                f = self._flags(args[2:], {'--chat-id', '--message-id', '--text', '--msg-type', '--content', '--idempotency-key', '--reply-in-thread'})
                kind = f.get('--msg-type', 'text')
                if kind not in ('text', 'interactive') or (('--text' in f) == ('--content' in f)):
                    raise ValueError
                content = json.dumps({'text': f['--text']}, ensure_ascii=False) if '--text' in f else f['--content']
                body = {'msg_type': kind, 'content': content, 'uuid': f['--idempotency-key']}
                if args[1] == '+messages-send':
                    body['receive_id'] = f['--chat-id']
                    return self._http_request('POST', '/open-apis/im/v1/messages', {'receive_id_type': 'chat_id'}, body)
                body['reply_in_thread'] = bool(f.get('--reply-in-thread'))
                return self._http_request('POST', '/open-apis/im/v1/messages/' + quote(f['--message-id'], safe='') + '/reply', data=body)
            if args[:2] == ['im', 'reactions'] and len(args) >= 3:
                f = self._flags(args[3:], {'--params', '--data'})
                params = json.loads(f.get('--params', '{}'))
                data = json.loads(f['--data']) if '--data' in f else None
                if args[2] == 'batch_query':
                    return self._http_request('POST', '/open-apis/im/v1/messages/reactions/batch_query', params, data)
                path = '/open-apis/im/v1/messages/' + quote(params['message_id'], safe='') + '/reactions'
                if args[2] == 'create': return self._http_request('POST', path, data=data)
                if args[2] == 'delete': return self._http_request('DELETE', path + '/' + quote(params['reaction_id'], safe=''))
                raise ValueError
            if args[:2] == ['im', '+chat-members-list']:
                f = self._flags(args[2:], {'--chat-id', '--member-id-type', '--member-types', '--page-all', '--page-limit'})
                return self._http_pages('/open-apis/im/v1/chats/' + quote(f['--chat-id'], safe='') + '/members/list',
                    {'member_id_type': f.get('--member-id-type', 'open_id'), 'member_types': f.get('--member-types', 'user,bot'), 'page_size': 100},
                    members=True, limit=int(f.get('--page-limit', 0)) or 1000)
            if args[:2] in (['im', '+chat-messages-list'], ['im', '+threads-messages-list']):
                f = self._flags(args[2:], {'--chat-id', '--thread', '--order', '--no-reactions', '--page-all', '--page-limit', '--start'})
                thread = args[1] == '+threads-messages-list'
                container = f['--thread'] if thread else f['--chat-id']
                if thread and not container.startswith('omt_'):
                    row = self.message_view(container, 'open_id')
                    if row is None or not row.get('thread_id'):
                        raise BotCliError(what, 404, 'not_found', definite=True)
                    container = row['thread_id']
                params = {'container_id_type': 'thread' if thread else 'chat', 'container_id': container,
                          'sort_type': 'ByCreateTimeDesc' if f.get('--order') == 'desc' else 'ByCreateTimeAsc',
                          'page_size': 50, 'card_msg_content_type': 'user_card_content', 'with_sender_name': True}
                if not thread: params['only_thread_root_messages'] = True
                if '--start' in f: params['start_time'] = str(int(datetime.fromisoformat(f['--start']).timestamp()))
                return self._http_pages('/open-apis/im/v1/messages', params, messages=True,
                                        limit=int(f.get('--page-limit', gs.FEISHU_PAGE_LIMIT)))
            if args[:3] == ['im', 'chats', 'list']:
                f = self._flags(args[3:], {'--params', '--page-all', '--page-limit'})
                params = json.loads(f.get('--params', '{}'))
                params.setdefault('page_size', 100)
                return self._http_pages('/open-apis/im/v1/chats', params, limit=int(f.get('--page-limit', 1000)))
        except (KeyError, ValueError, TypeError, OverflowError):
            raise failure('HTTP 请求参数或消息内容无效', '检查 bot API 请求，不能静默切换身份或传输') from None
        raise failure('HTTP 模式尚不支持这个命令', '使用已支持的 bot API；媒体操作才使用显式 bot CLI')

    def _http_pages(self, path, params, *, members=False, messages=False, limit=1000):
        if type(limit) is not int or not 0 < limit <= 1000:
            raise failure('分页范围不安全', '使用有限的完整分页范围')
        output, tokens, ids = {}, set(), set()
        complete, unsafe = False, False
        keys = ('users', 'bots') if members else ('items',)
        for key in keys: output[key] = []
        for _ in range(limit):
            data = self._http_request('GET', path, params)['data']
            for key in keys:
                rows = data.get(key, [] if members else None)
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    unsafe = True; continue
                for row in rows:
                    identity = (key, row.get('member_id') if members else row.get('message_id') if messages else row.get('chat_id'))
                    if not isinstance(identity[1], str) or not identity[1]:
                        unsafe = True
                    elif identity in ids: unsafe = True
                    else: ids.add(identity)
                output[key].extend(rows)
            if members:
                truncations = data.get('truncations')
                if not isinstance(truncations, list) or truncations: unsafe = True
                for key in ('user_total', 'bot_total'):
                    if key in data:
                        if key in output and output[key] != data[key]: unsafe = True
                        output[key] = data[key]
            more, token = data.get('has_more'), data.get('page_token')
            if type(more) is not bool: break
            if not more:
                complete = not unsafe; break
            if not isinstance(token, str) or not token or token in tokens: break
            tokens.add(token); params = {**params, 'page_token': token}
        output['has_more'] = not complete
        output['page_token'] = '' if complete else data.get('page_token', '')
        if members: output['truncations'] = ['incomplete'] if unsafe else []
        if messages:
            rows = []
            for row in output.pop('items'):
                try: rows.append(self._normalize_message(row))
                except (ValueError, TypeError, KeyError, OverflowError, OSError, AttributeError): complete = False
            output['messages'] = rows
            output['has_more'] = not complete
        if not complete: self._partial()
        return {'ok': True, 'identity': 'bot', 'data': output, 'meta': {'pagination': {'complete': complete}}}

    @staticmethod
    def _normalize_message(row):
        result = dict(row)
        # Preserve body/root/parent/app fields for mapping proof. Only list output
        # receives CLI-compatible content and minute UTC timestamps.
        for key in ('create_time', 'update_time'):
            if key in result:
                result[key] = datetime.fromtimestamp(int(result[key]) / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M')
        mentions = []
        raw_mentions = row.get('mentions', [])
        if not isinstance(raw_mentions, list): raise ValueError
        for mention in raw_mentions:
            if not isinstance(mention, dict): raise ValueError
            ident = mention.get('id')
            ident = ident.get('open_id') if isinstance(ident, dict) else ident
            if not isinstance(ident, str) or not ident or not isinstance(mention.get('key'), str): raise ValueError
            mentions.append({**mention, 'id': ident})
        result['mentions'] = mentions
        # Feishu's own system entries have an empty sender object. They are
        # never human sources, but must not make a complete page incomplete.
        if row.get('msg_type') == 'system':
            if 'create_time' not in result: raise ValueError
            result['content'] = ''
            return result
        if not isinstance(row.get('sender'), dict): raise ValueError
        sender = dict(row['sender'])
        if sender.get('sender_type') not in ('user', 'app', 'bot', 'system'): raise ValueError
        if not isinstance(sender.get('id'), str) or not sender['id']: raise ValueError
        if row.get('sender_name'): sender['name'] = row['sender_name']
        result['sender'] = sender
        kind = row.get('msg_type')
        if 'create_time' not in result: raise ValueError
        if row.get('deleted') is True or kind == 'system':
            result['content'] = ''
            return result
        raw = json.loads(row['body']['content'])
        if kind == 'text':
            content = raw['text']
            if not isinstance(content, str): raise ValueError
        elif kind == 'image': content = '[Image: ' + raw['image_key'] + ']'
        elif kind == 'post':
            body = raw if 'content' in raw or 'content_v2' in raw else next((raw[l] for l in ('zh_cn', 'en_us', 'ja_jp') if l in raw), None)
            if not isinstance(body, dict) or raw.get('files'): raise ValueError
            lines = [body.get('title', '')]
            for para in body.get('content_v2') or body.get('content') or []:
                text = ''
                for element in para:
                    tag = element.get('tag')
                    if tag in ('text', 'md'): text += element['text']
                    elif tag == 'img': text += '[Image: ' + element['image_key'] + ']'
                    elif tag == 'a': text += '[' + element['text'] + '](' + element['href'] + ')'
                    elif tag == 'at': text += '<at user_id="' + html.escape(element['user_id'], quote=True) + '">' + html.escape(element.get('user_name', '')) + '</at>'
                    elif tag == 'emotion': text += ':' + element['emoji_type'] + ':'
                    else: raise ValueError
                lines.append(text)
            content = '\n'.join(lines).strip()
        elif kind == 'interactive' and sender.get('sender_type') in ('app', 'bot'):
            content = ''  # Raw card retained; bot messages are not human sources.
        elif row.get('deleted') is True or kind == 'system': content = ''
        else: raise ValueError  # Never mark unsupported attachments delivered.
        for mention in mentions:
            if mention.get('key') and isinstance(mention.get('id'), str):
                replacement = '<at user_id="' + html.escape(mention['id'], quote=True) + '">' + html.escape(mention.get('name') or '') + '</at>'
                content = content.replace(mention['key'], replacement)
        result['content'] = content
        return result

    def user_info(self):
        raise failure('hostd 不读取个人登录信息', '使用已验证的 union_id 绑定确定人的身份')

    def search_user(self, email):
        raise failure('hostd 暂不支持 email 身份模式', '完成 union_id 迁移后重试')

    def _partial(self):
        if self.active_phase in ('feishu', 'buzz', 'members'):
            self.partial_reads.add(self.active_phase)

    def _read_call(self, what, args, **kwargs):
        try:
            return self.call(what, args, **kwargs)
        except gs.CliError as exc:
            if (getattr(exc, 'http_status', None) == 404 and getattr(exc, 'phase', None) == 'api'
                    and exc.kind == 'validation'):
                exc.kind = 'not_found'
            if exc.kind != 'not_found':
                self._partial()
            raise
        except (gs.GroupSyncError, OSError):
            self._partial()
            raise

    def _read_page(self, payload):
        rows, partial = self._message_page(payload)
        if partial:
            self._partial()
        return rows, partial

    def chat(self, chat_id, user_id_type='open_id'):
        return self._read_call('chat get', ['api', 'GET', f'/open-apis/im/v1/chats/{chat_id}', '--params', json.dumps({'user_id_type': user_id_type})])

    def member_listing(self, chat_id, user_id_type='open_id'):
        def bucket(id_type, kind):
            payload = self._read_call('chat members', ['im', '+chat-members-list', '--chat-id', chat_id,
                '--member-id-type', id_type, '--member-types', kind, '--page-all', '--page-limit', '0'], full=True)
            data = payload['data']
            pagination = (payload.get('meta') or {}).get('pagination') or {}
            kinds = ('user', 'bot') if kind == 'user,bot' else (kind,)
            complete = self._listing_complete(data, kinds) and pagination.get('complete') is not False
            return data, complete
        if user_id_type == 'open_id':
            users, complete = bucket('open_id', 'user,bot')
            bots = users
        elif user_id_type == 'union_id':
            users, users_complete = bucket('union_id', 'user')
            bots, bots_complete = bucket('open_id', 'bot')
            complete = users_complete and bots_complete
        else:
            raise failure('成员身份类型不受支持', '使用 union_id 人员映射或本应用 open_id')
        user_rows = users.get('users') or []
        bot_rows = bots.get('bots') or []
        user_pattern = gs.UNION_ID_RE if user_id_type == 'union_id' else gs.OPEN_ID_RE
        valid_users = [row for row in user_rows if isinstance(row, dict) and isinstance(row.get('member_id'), str) and user_pattern.fullmatch(row['member_id'])]
        valid_bots = [row for row in bot_rows if isinstance(row, dict) and isinstance(row.get('app_id'), str)
                      and gs.APP_ID_RE.fullmatch(row['app_id']) and isinstance(row.get('member_id'), str)
                      and gs.OPEN_ID_RE.fullmatch(row['member_id'])]
        opaque = [row for row in bot_rows if isinstance(row, dict) and 'app_id' not in row
                  and isinstance(row.get('member_id'), str) and gs.OPEN_ID_RE.fullmatch(row['member_id'])]
        opaque_ids = frozenset(row['member_id'] for row in opaque)
        users_map = {row['member_id']: str(row.get('name') or '') for row in valid_users}
        bots_map = {row['app_id']: row['member_id'] for row in valid_bots}
        complete = (complete and len(valid_users) == len(user_rows) == len(users_map)
                    and len(valid_bots) == len(bots_map)
                    and len(valid_bots) + len(opaque) == len(bot_rows)
                    and len(opaque_ids) == len(opaque)
                    and len({row['member_id'] for row in [*valid_bots, *opaque]}) == len(bot_rows))
        if not complete:
            self._partial()
        return BotMemberListing(users_map, bots_map, complete, opaque_ids)

    def change_members(self, method, chat_id, ids, id_type):
        if method not in ('POST', 'DELETE') or id_type not in ('open_id', 'union_id', 'app_id'):
            raise failure('成员变更请求无效', '检查成员操作和身份类型')
        # A partial snapshot cannot justify additions, removals, or capacity calculations.
        listing = self.member_listing(chat_id, 'union_id' if id_type == 'union_id' else 'open_id')
        if not listing.complete:
            raise failure('群成员列表不完整，已停止成员变更', '检查分页和群安全限制，获得完整名单后重试')
        params = {'member_id_type': id_type}
        if method == 'POST':
            params['succeed_type'] = 1
        data = self.call('chat members change', ['api', method, f'/open-apis/im/v1/chats/{chat_id}/members',
                                                '--params', json.dumps(params), '--data', json.dumps({'id_list': ids})])
        return sum(len(data.get(key) or []) for key in gs.MEMBER_FAILURE_LISTS)

    @staticmethod
    def _message_page(payload):
        data = payload.get('data') or {}
        meta = payload.get('meta') or {}
        rows = data.get('messages')
        complete = data.get('has_more') is False and (meta.get('pagination') or {}).get('complete') is not False
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return [], True
        return rows, not complete

    def message_page(self, chat_id, start, end, *, token=''):
        """A complete positive page, not proof of interval completeness.

        GET /im/v1/messages supports second-based start/end and opaque tokens;
        sort_type and the entire query stay fixed for the traversal.
        """
        try:
            if (not isinstance(chat_id, str) or not re.fullmatch(r'oc_[A-Za-z0-9_]+', chat_id)
                    or type(start) is not int or type(end) is not int or not 0 <= start <= end
                    or not isinstance(token, str) or len(token) > 4096):
                raise ValueError
            params = dict(container_id_type='chat', container_id=chat_id,
                          start_time=str(start), end_time=str(end), sort_type='ByCreateTimeAsc',
                          page_size=50, card_msg_content_type='user_card_content',
                          only_thread_root_messages=True, with_sender_name=True)
            if token: params['page_token'] = token
            data = self._read_call('message page', ['api', 'GET', '/open-apis/im/v1/messages',
                                  '--params', json.dumps(params)])
            rows, more, next_token = data.get('items'), data.get('has_more'), data.get('page_token', '')
            if (not isinstance(rows, list) or len(rows) > 50 or type(more) is not bool
                    or not isinstance(next_token, str) or len(next_token) > 4096
                    or more and (not rows or not next_token or next_token == token)):
                raise ValueError
            ids = set()
            for row in rows:
                if (not isinstance(row, dict) or row.get('chat_id') != chat_id
                        or not isinstance(row.get('message_id'), str)
                        or not gs.MESSAGE_ID_RE.fullmatch(row['message_id']) or row['message_id'] in ids
                        or not isinstance(row.get('create_time'), str) or not row['create_time'].isdigit()
                        or not start * 1000 <= int(row['create_time']) < (end + 1) * 1000):
                    raise ValueError
                # Body support is checked only by the fresh per-source read.
                # Positive metadata retains work; it never authorizes a send.
                ids.add(row['message_id'])
            return MessagePage(tuple(rows), more, next_token if more else '')
        except (ValueError, TypeError, KeyError, OverflowError, gs.GroupSyncError, gs.CliError):
            self._partial()
            raise failure('飞书历史分页尚未完整核验', '保留扫描位置与待办，检查同一应用的分页读取后重试') from None

    def messages(self, chat_id, start, *, order='asc', page_limit=gs.FEISHU_PAGE_LIMIT):
        payload = self._read_call('chat messages', ['im', '+chat-messages-list', '--chat-id', chat_id, '--order', order,
            '--no-reactions', '--page-all', '--page-limit', str(page_limit), '--start', start.isoformat()], full=True)
        return self._read_page(payload)

    def thread_messages(self, root_message_id):
        try:
            payload = self._read_call('thread messages', ['im', '+threads-messages-list', '--thread', root_message_id,
                '--order', 'desc', '--no-reactions', '--page-all', '--page-limit', str(gs.THREAD_PAGE_LIMIT)], full=True)
        except gs.CliError as exc:
            if exc.kind == 'not_found':
                return [], False
            raise
        return self._read_page(payload)

    def message_view(self, message_id, user_id_type):
        if not gs.MESSAGE_ID_RE.fullmatch(message_id):
            return None
        if user_id_type not in ('open_id', 'union_id'):
            raise failure('消息身份类型不受支持', '使用本应用 open_id 或验证后的 union_id')
        try:
            data = self._read_call('message get', ['api', 'GET', f'/open-apis/im/v1/messages/{message_id}',
                                           '--params', json.dumps({'user_id_type': user_id_type, 'card_msg_content_type': 'user_card_content'})])
        except gs.CliError as exc:
            if exc.kind == 'not_found':
                return None
            raise
        rows = data.get('items')
        if rows == []:
            return None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get('message_id') != message_id:
            raise failure('单条消息应答与目标不一致', '检查消息读取结果，不能借用其他消息的身份')
        return rows[0]

    def root_for_event(self, chat_id, event, *, observed_message=None):
        """Read a reply/reaction target and its actual root, rejecting cross-chat projections."""
        mid = event.get('message_id') or event.get('root_id')
        if not isinstance(mid, str) or not gs.MESSAGE_ID_RE.fullmatch(mid):
            raise failure('事件没有可验证的消息目标', '重新读取事件对应的消息')
        # A caller in this round may already have performed this exact
        # same-reader open_id GET. Never take a snapshot from the event/IPC.
        message = observed_message if observed_message is not None else self.message_view(mid, 'open_id')
        if message is None:
            return None
        if not isinstance(message, dict) or message.get('message_id') != mid:
            raise failure('事件目标与实际读取消息不一致', '重新读取事件对应的消息')
        if message.get('chat_id') != chat_id:
            raise failure('事件目标不属于这个群', '检查消息与群的绑定')
        root_id = message.get('root_id') or message['message_id']
        if event.get('root_id') and event['root_id'] != root_id:
            raise failure('事件中的根消息不一致', '重新读取真实消息与话题关系')
        root = self.message_view(root_id, 'open_id') if root_id != mid else message
        if root is None:
            return None
        if root.get('chat_id') != chat_id or root.get('root_id') not in (None, '', root_id):
            raise failure('根消息不属于同一个群或话题', '检查话题根消息的真实关系')
        return root

    @staticmethod
    def _image_key(key):
        return (type(key) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}', key) is not None)

    @staticmethod
    def _image_body(raw, mime):
        if (type(raw) is not bytes or not 1 <= len(raw) <= 10_000_000
                or mime not in ('image/png', 'image/jpeg')
                or not (raw.startswith(b'\x89PNG\r\n\x1a\n') if mime == 'image/png'
                        else raw.startswith(b'\xff\xd8\xff'))):
            raise failure('图片内容无法完整核验', '检查图片 MIME、长度和完整原始字节')
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _image_scratch(path):
        value = path.lstat()
        if (not stat.S_ISDIR(value.st_mode) or value.st_uid != os.getuid()
                or stat.S_IMODE(value.st_mode) != 0o700):
            raise failure('图片临时目录不安全', '使用本机用户独占的 0700 临时目录')
        return value.st_dev, value.st_ino

    def upload_image(self, raw, mime):
        """Normal own-bot upload envelope via protected CLI/scheduler only."""
        self._image_body(raw, mime)
        self.identity()
        with tempfile.TemporaryDirectory(prefix='hostd-image-upload-') as name:
            scratch = Path(name)
            before = self._image_scratch(scratch)
            path = scratch / ('image.png' if mime == 'image/png' else 'image.jpg')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(raw)
            # Pin the exact regular owned input before dispatch, including its
            # byte hash. No caller file path reaches this internal wrapper.
            if read_owned(path, max_bytes=10_000_000) != raw:
                raise failure('图片上传输入发生变化', '重新核验原始图片，不要重试未知上传')
            payload = self.call('own image upload', ['im', 'images', 'create',
                '--data', '{"image_type":"message"}', '--file', 'image=' + path.name,
                '--as', 'bot'], cwd=scratch, full=True, private_files=True)
            self.identity()
            if (self._image_scratch(scratch) != before
                    or type(payload) is not dict or payload.get('ok') is not True
                    or payload.get('identity') != 'bot' or type(payload.get('data')) is not dict
                    or not self._image_key(payload['data'].get('image_key'))):
                raise failure('图片上传结果尚未核实', '核查原上传账本，不要重复上传或猜测 image key')
            return payload['data']['image_key']

    def own_image(self, requested_key):
        """Exact own-key GET's real three-field binary envelope, never generic call."""
        if not self._image_key(requested_key):
            raise failure('图片回执标识无效', '仅核验原上传已固定的标识')
        self.identity()
        with tempfile.TemporaryDirectory(prefix='hostd-image-readback-') as name:
            scratch = Path(name)
            before = self._image_scratch(scratch)
            output = scratch / 'image.bin'
            # CLI creates this fresh file under its child-only 077 umask.
            result, _ = self._run('own image readback', ['api', 'GET',
                '/open-apis/im/v1/images/' + quote(requested_key, safe=''),
                '--output', output.name, '--as', 'bot'], scratch, private_files=True,
                raw_image_envelope=True)
            self.identity()
            def unique_object(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError('duplicate binary envelope field')
                    value[key] = item
                return value
            try:
                stdout = result.stdout
                encoded_stdout = stdout.encode('utf-8') if type(stdout) is str else b''
                if not encoded_stdout or len(encoded_stdout) > 65536:
                    raise ValueError('invalid binary envelope size')
                payload = json.loads(stdout, object_pairs_hook=unique_object)
            except Exception:
                raise failure('图片回读结果尚未完整核实', '仅接受标准输出中的唯一三字段图片回执') from None
            if (self._image_scratch(scratch) != before or result.returncode != 0
                    or type(payload) is not dict or set(payload) != {'saved_path', 'size_bytes', 'content_type'}
                    or type(payload['saved_path']) is not str or payload['saved_path'] != str(output.absolute())
                    or type(payload['size_bytes']) is not int or not 1 <= payload['size_bytes'] <= 10_000_000
                    or payload['content_type'] not in ('image/png', 'image/jpeg')):
                raise failure('图片回读结果尚未完整核实', '核查原标识、自己的应用和完整二进制回执')
            raw = read_owned(output, max_bytes=10_000_000)
            if len(raw) != payload['size_bytes']:
                raise failure('图片回读长度不一致', '等待完整原图片回读，不要重新上传')
            digest = self._image_body(raw, payload['content_type'])
            return OwnImage(requested_key, payload['content_type'], len(raw), digest, raw)

    def download_image(self, message_id, key, workdir):
        if not gs.MESSAGE_ID_RE.fullmatch(message_id) or not gs.FEISHU_IMAGE_KEY_RE.fullmatch(key):
            raise gs.CliError('resource download', 1, 'bad id', definite=True)
        if list(workdir.iterdir()):
            raise failure('图片下载目录不为空', '使用新建的私有临时目录')
        self.call('resource download', ['im', '+messages-resources-download', '--message-id', message_id,
            '--file-key', key, '--type', 'image', '--output', 'download'], cwd=workdir)
        found = list(workdir.iterdir())
        if len(found) != 1 or not stat.S_ISREG(found[0].lstat().st_mode) or found[0].stat().st_uid != os.getuid():
            raise failure('图片下载没有得到可验证的本地文件', '检查下载目录与附件权限')
        return found[0]

    def own_reactions(self, message_id, emoji, *, expected_reaction_id=""):
        """Bounded actual list read; completeness never implies unsent writes.

        The installed official im.reactions.list schema returns bot app IDs in
        operator_id and opaque reaction_id records. A missing visibility/SLA
        guarantee means an empty snapshot cannot authorize an unknown create.
        """
        if (not isinstance(message_id, str) or not gs.MESSAGE_ID_RE.fullmatch(message_id)
                or not isinstance(emoji, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', emoji)
                or not isinstance(expected_reaction_id, str)
                or (expected_reaction_id and not re.fullmatch(r'(?:[A-Za-z0-9_.:-]{1,256}|[A-Za-z0-9_-]{85}[AQgw]==)', expected_reaction_id))):
            raise failure('表情读回目标无效', '核对原消息、自己的应用与固定表情类型')
        self.identity()
        params = {'reaction_type': emoji, 'user_id_type': 'open_id', 'page_size': 50}
        path = '/open-apis/im/v1/messages/' + quote(message_id, safe='') + '/reactions'
        seen, tokens, own = set(), set(), []
        for _ in range(1 + gs.REACTION_PAGES_MAX):
            data = self._read_call('own reaction readback', ['api', 'GET', path, '--params', json.dumps(params), '--as', 'bot'])
            items, more = data.get('items'), data.get('has_more')
            valid = isinstance(items, list) and len(items) <= 50 and type(more) is bool
            if valid:
                for row in items:
                    operator = row.get('operator') if isinstance(row, dict) else None
                    kind = row.get('reaction_type') if isinstance(row, dict) else None
                    rid = row.get('reaction_id') if isinstance(row, dict) else None
                    if (not isinstance(operator, dict) or operator.get('operator_type') not in ('user', 'app')
                            or not isinstance(operator.get('operator_id'), str)
                            or not (gs.APP_ID_RE if operator['operator_type'] == 'app' else gs.OPEN_ID_RE).fullmatch(operator['operator_id'])
                            or not isinstance(kind, dict) or kind.get('emoji_type') != emoji
                            or not isinstance(rid, str)
                            or not re.fullmatch(r'(?:[A-Za-z0-9_.:-]{1,256}|[A-Za-z0-9_-]{85}[AQgw]==)', rid)
                            or rid in seen):
                        valid = False
                        break
                    if rid == expected_reaction_id and (operator['operator_type'] != 'app' or operator['operator_id'] != self.app_id):
                        valid = False
                        break
                    seen.add(rid)
                    if operator['operator_type'] == 'app' and operator['operator_id'] == self.app_id:
                        own.append(rid)
            if not valid:
                break
            if not more:
                # More than one own record for the exact emoji is ambiguous.
                if len(own) <= 1:
                    return ReactionSnapshot(message_id, self.app_id, emoji, True, tuple(own))
                break
            token = data.get('page_token')
            if (not isinstance(token, str) or not 0 < len(token) <= 8192
                    or any(ord(c) < 33 or ord(c) == 127 for c in token) or token in tokens):
                break
            tokens.add(token)
            params = {**params, 'page_token': token}
        self._partial()
        return ReactionSnapshot(message_id, self.app_id, emoji, False, ())

    def reaction_details(self, message_ids, user_id_type):
        if user_id_type not in ('union_id', 'open_id'):
            raise failure('表情身份类型不受支持', '使用验证后的 union_id 或本应用 open_id')
        requested = set(message_ids)
        pending = {mid: {'message_id': mid} for mid in requested}
        collected, complete, seen_tokens = {}, {}, {}
        for _ in range(1 + gs.REACTION_PAGES_MAX):
            if not pending:
                break
            data = self._read_call('reactions query', ['im', 'reactions', 'batch_query', '--params', json.dumps({'user_id_type': user_id_type}),
                 '--data', json.dumps({'queries': list(pending.values()), 'page_size_per_message': 10})])
            details = data.get('success_msg_reaction_details')
            failures = data.get('fail_msg_reaction_details') or []
            if not isinstance(details, list) or not isinstance(failures, list):
                break
            failed = {r.get('message_id') for r in failures if isinstance(r, dict)}
            counts = Counter(r.get('message_id') for r in details if isinstance(r, dict) and isinstance(r.get('message_id'), str))
            failed.update(mid for mid, count in counts.items() if count > 1)
            next_queries, observed = {}, set()
            for row in details:
                if not isinstance(row, dict):
                    continue
                mid = row.get('message_id')
                if not isinstance(mid, str) or mid not in pending or mid in observed or mid in failed:
                    continue
                observed.add(mid)
                items = row.get('message_reaction_items')
                if not isinstance(items, list) or type(row.get('has_more')) is not bool:
                    continue
                tuples = []
                valid = True
                for item in items:
                    operator = item.get('operator') if isinstance(item, dict) else None
                    if not isinstance(operator, dict) or operator.get('operator_type') not in ('user', 'app') or not operator.get('operator_id') or not item.get('emoji_type'):
                        valid = False
                        break
                    tuples.append((operator['operator_type'], operator['operator_id'], item['emoji_type']))
                if not valid:
                    continue
                collected.setdefault(mid, []).extend(tuples)
                if row['has_more']:
                    token = row.get('page_token')
                    seen = seen_tokens.setdefault(mid, set())
                    if isinstance(token, str) and token and token not in seen:
                        seen.add(token)
                        next_queries[mid] = {'message_id': mid, 'page_token': token}
                else:
                    complete[mid] = collected[mid]
            pending = next_queries
        if requested - complete.keys():
            self._partial()
        return complete  # Omit every failed, malformed, missing or truncated result.


def build_clients(cfg, base_env, *, runner=subprocess.run, http=None, trusted_relays=(), http_pool=None, scheduler=None):
    """API-compatible gs.Clients; its historical `owner` slot is the selected sync bot."""
    mirror = {}
    for line in read_owned(cfg['mirror_env_file']).decode().splitlines():
        key, separator, value = line.partition('=')
        if separator and key.strip() in gs.BUZZ_ENV_KEYS:
            mirror[key.strip()] = value.strip().strip('"').strip("'")
    key = gs.secret_hex(mirror.get('BUZZ_PRIVATE_KEY', ''), 'mirror key')
    if gs._signer_pubkey(key) != cfg['mirror_pubkey']:
        raise failure('镜像签名身份与绑定不一致', '修复本机镜像凭据与绑定')
    relay = _trusted_relay(mirror.get('BUZZ_RELAY_URL', ''), trusted_relays)
    mirror['BUZZ_RELAY_URL'] = relay
    agents = {a['app_id']: BotLarkCli(a['app_id'], a['lark_config_dir'], a['lark_data_dir'], base_env=base_env, runner=runner, http_pool=http_pool, scheduler=scheduler, chat_id=cfg.get('chat_id'))
              for a in cfg['agents'].values()}
    sync_app = cfg['agents'][cfg['desk_pubkey']]['app_id']
    return gs.Clients(owner=agents[sync_app], agents=agents,
        buzz=gs.BuzzCli(cfg['buzz_cli'], gs.child_env(base_env, mirror), runner=runner), http=http or gs._http_get,
        relay_url=relay, mirror_key=key, mirror_auth_tag=mirror.get('BUZZ_AUTH_TAG', ''), home=str(base_env.get('HOME') or ''))
