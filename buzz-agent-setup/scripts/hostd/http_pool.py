"""Process-shared, bot-only Feishu HTTPS connections and in-memory tenant tokens.

Create one HttpPool for hostd and inject it into converted BotLarkCli API calls.
There is no personal identity, redirect, automatic business retry or disk token
cache. Secrets are read only through the protected existing secret store.
The default transport is stdlib HTTPSConnection with verified TLS; transport
injection exists for offline tests. Neither tokens nor peer text enter errors.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import http.client
import json
import math
from pathlib import Path
import re
import ssl
import threading
import time
from urllib.parse import unquote, urlencode, urlsplit

import buzz_feishu_group_sync as gs
try:
    from .safety import read_owned
    from .scheduler import AdmissionError
except ImportError:
    from safety import read_owned
    from scheduler import AdmissionError

HOST = 'open.feishu.cn'
TOKEN_PATH = '/open-apis/auth/v3/tenant_access_token/internal'
AUTH_CODES = frozenset({99991661, 99991671, 99991668, 99991663, 99991677})
PERMISSION_CODES = frozenset({99991672, 99991676, 99991679, 230027, 99991673, 99991662})
CLIENT_CODES = frozenset({10014, 99991543})
NOTICE = ('bot HTTP 请求尚未确认，已保留原投递记录。\n'
          '怎么解决：检查本机应用配置、权限与网络；结果未知时先核实实际回执，再使用原投递账本重试。\n'
          '复制给 AI：帮我检查 hostd 的 bot HTTP 池、应用权限、凭据文件权限和投递回执；不要输出密钥、token 或消息正文。')

HTTP_STAGES = frozenset({'acquire', 'request', 'headers', 'read', 'decode', 'shape'})
HTTP_EXCEPTIONS = frozenset({'none', 'timeout', 'disconnected', 'incomplete_read',
                            'reset', 'tls', 'os', 'value', 'other'})
HTTP_DIAGNOSTIC_KEYS = frozenset({'stage', 'exception', 'phase', 'http_status', 'api_code', 'elapsed_ms'})
MAX_DIAGNOSTIC_MS = 86_400_000


def valid_http_diagnostic(value) -> bool:
    """Only fixed enums and bounded plain integers may cross the status boundary."""
    return (type(value) is dict and set(value) == HTTP_DIAGNOSTIC_KEYS
            and type(value['stage']) is str and value['stage'] in HTTP_STAGES
            and type(value['exception']) is str and value['exception'] in HTTP_EXCEPTIONS
            and type(value['phase']) is str and value['phase'] in {'api', 'token'}
            and type(value['http_status']) is int
            and (value['http_status'] == -1 or 100 <= value['http_status'] <= 599)
            and type(value['api_code']) is int and -1 <= value['api_code'] <= 2**31 - 1
            and type(value['elapsed_ms']) is int and 0 <= value['elapsed_ms'] <= MAX_DIAGNOSTIC_MS)


def _diagnostic_clock():
    # Independent observation clock: do not consume injected transport deadlines.
    try:
        return time.monotonic()
    except Exception:
        return None


def _http_diagnostic(error, stage, cause, started):
    """Best effort only. Never inspect exception text, args, context or locals."""
    try:
        classes = (('timeout', TimeoutError), ('disconnected', http.client.RemoteDisconnected),
                   ('incomplete_read', http.client.IncompleteRead),
                   ('reset', (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)),
                   ('tls', ssl.SSLError), ('os', OSError),
                   ('value', (ValueError, TypeError, RecursionError)))
        label = 'none' if cause is None else next(
            (name for name, kind in classes if isinstance(cause, kind)), 'other')
        status, code = error.http_status, error.code
        value = {'stage': stage, 'exception': label, 'phase': error.phase,
                 'http_status': status if type(status) is int and 100 <= status <= 599 else -1,
                 'api_code': code if type(code) is int and -1 <= code <= 2**31 - 1 else -1,
                 'elapsed_ms': min(MAX_DIAGNOSTIC_MS, max(0, int((time.monotonic() - started) * 1000)))}
        if valid_http_diagnostic(value):
            error.http_diagnostic = value
    except Exception:
        pass  # Observation failure must not replace an original business result.


class PoolError(gs.CliError):
    """Fixed notice; outcome metadata remains typed, in memory only."""
    def __init__(self, *, code=-1, http_status=-1, kind='network', definite=False, dispatched=False, phase='api', retry_after=0):
        error_type = ('validation' if kind == 'configuration' else kind if kind in
                      ('authentication', 'permission', 'validation') else 'api' if definite else 'network')
        super().__init__('bot HTTP', code, kind, definite=definite, error_type=error_type)
        self.http_status, self.dispatched, self.phase = http_status, dispatched, phase
        self.retry_after = retry_after
        self.args = (NOTICE,)
    def __str__(self): return NOTICE


@dataclass
class _Flight:
    done: threading.Event = field(default_factory=threading.Event)
    waiters: int = 0
    token: str | None = field(default=None, repr=False)
    error: PoolError | None = None


class _App:
    def __init__(self, app_id, config, data):
        self.app_id = app_id
        self.config, self.data = config, data
        self.condition = threading.Condition()
        self.token, self.refresh_at = None, 0
        self.flight, self.closed = None, False
        self.idle, self.created, self.active = deque(), 0, 0


class HttpPool:
    def __init__(self, *, connection_factory=None, clock=time.monotonic, timeout=90,
                 scheduler=None, max_connections=4, max_response_bytes=8 * 1024 * 1024, max_request_bytes=8 * 1024 * 1024):
        if (isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0
                or any(type(v) is not int or v < 1 for v in (max_connections, max_response_bytes, max_request_bytes))):
            raise PoolError(kind='validation', definite=True)
        self.scheduler = scheduler
        self.factory = connection_factory or http.client.HTTPSConnection
        self.clock, self.timeout, self.max_connections = clock, timeout, max_connections
        self.max_response_bytes, self.max_request_bytes = max_response_bytes, max_request_bytes
        self.context = ssl.create_default_context()
        self.lock, self.apps, self.closed = threading.Lock(), {}, False

    def _scope(self, app_id, config_dir, data_dir):
        try:
            config, data = Path(config_dir), Path(data_dir)
            if not isinstance(app_id, str) or re.fullmatch(r'cli_[A-Za-z0-9]+', app_id) is None or not config.is_absolute() or not data.is_absolute():
                raise ValueError
            if any(part in ('.', '..') for path in (config, data) for part in path.parts):
                raise ValueError
            doc = json.loads(read_owned(config / 'config.json'))
            apps = doc.get('apps') if isinstance(doc, dict) else None
            if (not isinstance(apps, list) or any(not isinstance(a, dict) for a in apps)
                    or sum(a.get('appId') == app_id for a in apps) != 1):
                raise ValueError
            selected = next(a for a in apps if a.get('appId') == app_id)
            if selected.get('brand', doc.get('brand', 'feishu')) != 'feishu':
                raise ValueError
            with self.lock:
                if self.closed: raise ValueError
                app = self.apps.get(app_id)
                if app is None:
                    app = self.apps[app_id] = _App(app_id, config, data)
                if (app.config, app.data) != (config, data): raise ValueError
            return app
        except Exception:
            raise PoolError(kind='validation', definite=True, phase='profile') from None

    def _request_shape(self, method, path, params, data):
        try:
            if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE') or not isinstance(path, str) or len(path) > 8192:
                raise ValueError
            parsed, decoded = urlsplit(path), unquote(path)
            if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not path.startswith('/open-apis/')
                    or any(ord(c) < 33 or c == '\\' for c in decoded) or '%' in decoded
                    or any(part in ('.', '..') for part in decoded.split('/'))
                    or decoded.startswith(('/open-apis/auth/', '/open-apis/authen/'))):
                raise ValueError
            if params is not None:
                if not isinstance(params, dict) or any(not isinstance(k, str) for k in params): raise ValueError
                cleaned = {}
                for key, value in params.items():
                    values = value if isinstance(value, (list, tuple)) else [value]
                    if any(not isinstance(v, (str, int, float, bool)) or (isinstance(v, float) and not math.isfinite(v)) for v in values): raise ValueError
                    cleaned[key] = ['true' if v is True else 'false' if v is False else v for v in values]
                query = urlencode(cleaned, doseq=True)
                path += '?' + query if query else ''
            body = None if data is None else json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
            if len(path) > 16384 or (body is not None and len(body) > self.max_request_bytes): raise ValueError
            return path, body
        except Exception:
            raise PoolError(kind='validation', definite=True, phase='validation') from None

    def stats(self, app_id):
        """Counts only; callers must apply their own app visibility policy."""
        with self.lock: app = self.apps.get(app_id)
        if app is None: return {'connections': 0, 'active': 0, 'refresh_waiters': 0}
        with app.condition:
            return {'connections': app.created, 'active': app.active,
                    'refresh_waiters': app.flight.waiters if app.flight else 0}

    def _acquire(self, app, deadline, phase):
        started = _diagnostic_clock()
        with app.condition:
            while True:
                remaining = deadline - self.clock()
                if app.closed or remaining <= 0:
                    error = PoolError(definite=True, phase=phase)
                    _http_diagnostic(error, 'acquire', None if app.closed else TimeoutError(), started)
                    raise error
                if app.idle:
                    app.active += 1
                    return app.idle.popleft()
                if app.created < self.max_connections:
                    app.created += 1; app.active += 1
                    break
                app.condition.wait(remaining)
        try:
            return self.factory(HOST, timeout=remaining, context=self.context)
        except Exception as cause:
            with app.condition:
                app.created -= 1; app.active -= 1; app.condition.notify_all()
            error = PoolError(definite=True, phase=phase)
            _http_diagnostic(error, 'acquire', cause, started)
            raise error from None

    def _release(self, app, connection, reusable):
        close = False
        with app.condition:
            app.active -= 1
            if reusable and not app.closed: app.idle.append(connection)
            else: app.created -= 1; close = True
            app.condition.notify_all()
        if close:
            try: connection.close()
            except Exception: pass

    @staticmethod
    def _retry_after(response):
        try:
            value = response.getheader('Retry-After')
            return int(value) if value is not None and value.isdigit() and 0 < int(value) <= 3600 else 0
        except Exception: return 0

    @staticmethod
    def _failure(status, payload, phase, retry_after):
        code = payload.get('code') if isinstance(payload, dict) and type(payload.get('code')) is int else -1
        if status == 429 or code in gs.LARK_RATE_LIMIT_CODES: kind, definite = 'rate_limited', True
        elif code in CLIENT_CODES: kind, definite = 'configuration', True
        elif status == 401 or code in AUTH_CODES: kind, definite = 'authentication', True
        elif status == 403 or code in PERMISSION_CODES: kind, definite = 'permission', True
        elif 400 <= status < 500: kind, definite = 'validation', True
        else: kind, definite = 'network', False  # Unknown API codes / 5xx never authorize a write replay.
        return PoolError(code=code, http_status=status, kind=kind, definite=definite or phase == 'token',
                         dispatched=phase == 'api', phase=phase, retry_after=retry_after)

    def _exchange(self, app, method, path, headers, body, deadline, phase, chat_id=None, priority='normal'):
        dispatch = lambda: self._exchange_transport(app, method, path, headers, body, deadline, phase)
        if self.scheduler is None:
            return dispatch()
        try:
            return self.scheduler.run(dispatch, app_id=app.app_id,
                                      chat_id=None if phase == 'token' else chat_id,
                                      priority=priority, timeout=max(.001, deadline - self.clock()))
        except AdmissionError:
            raise PoolError(kind='rate_limited', definite=True, dispatched=False, phase=phase) from None

    def _exchange_transport(self, app, method, path, headers, body, deadline, phase):
        started = _diagnostic_clock()
        connection = self._acquire(app, deadline, phase)
        reusable, status, code = False, -1, -1
        stage = 'request'
        try:
            connection.timeout = max(.001, deadline - self.clock())
            if getattr(connection, 'sock', None): connection.sock.settimeout(connection.timeout)
            connection.request(method, path, body=body, headers=headers)
            stage = 'headers'
            response = connection.getresponse()
            status = response.status
            if type(status) is not int or not 100 <= status <= 599: raise ValueError
            if 300 <= status < 400:
                raise PoolError(http_status=status, definite=phase == 'token', dispatched=phase == 'api', phase=phase)
            length = response.getheader('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > self.max_response_bytes): raise ValueError
            chunks, total = [], 0
            stage = 'read'
            while True:
                remaining = deadline - self.clock()
                if remaining <= 0: raise TimeoutError
                if getattr(connection, 'sock', None): connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, self.max_response_bytes + 1 - total))
                if not isinstance(chunk, bytes): raise ValueError
                if not chunk: break
                total += len(chunk)
                if total > self.max_response_bytes: raise ValueError
                chunks.append(chunk)
            raw = b''.join(chunks)
            stage = 'decode'
            try: payload = json.loads(raw) if raw else {} if status == 204 else None
            except (ValueError, UnicodeError, RecursionError):
                if status >= 400: payload = None
                else: raise ValueError from None
            stage = 'shape'
            if status >= 400:
                raise self._failure(status, payload, phase, self._retry_after(response))
            if not isinstance(payload, dict): raise ValueError
            code = payload.get('code', 0 if status == 204 else None)
            if type(code) is not int: raise ValueError
            if code != 0: raise self._failure(status, payload, phase, self._retry_after(response))
            if phase == 'api' and 'data' in payload and not isinstance(payload['data'], dict):
                raise ValueError
            reusable = not response.will_close
            return payload
        except PoolError as error:
            _http_diagnostic(error, stage, None, started)
            raise
        except Exception as cause:
            error = PoolError(code=code if type(code) is int else -1, http_status=status,
                              definite=phase == 'token', dispatched=phase == 'api', phase=phase)
            _http_diagnostic(error, stage, cause, started)
            raise error from None
        finally:
            self._release(app, connection, reusable)

    def _token(self, app_id, app, deadline, priority):
        with app.condition:
            if app.closed: raise PoolError(definite=True, phase='token')
            if app.token and self.clock() < app.refresh_at: return app.token
            flight = app.flight
            leader = flight is None
            if leader: flight = app.flight = _Flight()
            else: flight.waiters += 1
        if not leader:
            try:
                if not flight.done.wait(max(0, deadline - self.clock())):
                    raise PoolError(definite=True, phase='token')
                if flight.error: raise flight.error
                return flight.token
            finally:
                with app.condition: flight.waiters -= 1
        try:
            try:
                from .secrets_store import app_secret
            except ImportError:
                from secrets_store import app_secret
            secret = app_secret(app_id, str(app.config), str(app.data))
            started = self.clock()
            body = json.dumps({'app_id': app_id, 'app_secret': secret}, separators=(',', ':')).encode()
            payload = self._exchange(app, 'POST', TOKEN_PATH, {'Content-Type': 'application/json; charset=utf-8'}, body, deadline, 'token', priority=priority)
            token, expire = payload.get('tenant_access_token'), payload.get('expire')
            if (not isinstance(token, str) or not 0 < len(token) <= 8192 or any(c.isspace() or ord(c) < 33 for c in token)
                    or type(expire) is not int or not 0 < expire <= 7200):
                raise PoolError(definite=True, phase='token')
            with app.condition:
                if app.closed: raise PoolError(definite=True, phase='token')
                app.token, app.refresh_at = token, started + expire - min(60, expire / 10)
                flight.token = token
            return token
        except (Exception, SystemExit) as error:
            flight.error = error if isinstance(error, PoolError) else PoolError(kind='authentication', definite=True, phase='token')
            raise flight.error from None
        finally:
            with app.condition:
                app.flight = None
                flight.done.set(); app.condition.notify_all()

    def invalidate(self, app_id, *, token=None):
        with self.lock: app = self.apps.get(app_id)
        if app:
            with app.condition:
                if token is None or app.token == token: app.token, app.refresh_at = None, 0

    def request(self, app_id, config_dir, data_dir, method, path, params=None, data=None, *, chat_id=None, priority='normal'):
        path, body = self._request_shape(method, path, params, data)
        app = self._scope(app_id, config_dir, data_dir)
        deadline = self.clock() + self.timeout
        token = self._token(app_id, app, deadline, priority)
        try:
            payload = self._exchange(app, method, path, {'Authorization': 'Bearer ' + token,
                'Content-Type': 'application/json; charset=utf-8', 'Accept': 'application/json'}, body, deadline, 'api', chat_id=chat_id, priority=priority)
        except PoolError as error:
            if error.kind == 'authentication': self.invalidate(app_id, token=token)
            raise
        data = payload.get('data', {k: v for k, v in payload.items() if k not in ('code', 'msg')})
        if not isinstance(data, dict):
            raise PoolError(http_status=200, dispatched=True)
        return {'ok': True, 'identity': 'bot', 'data': data}

    def close(self):
        with self.lock:
            self.closed = True
            apps = list(self.apps.values())
        for app in apps:
            with app.condition:
                app.closed, app.token = True, None
                if app.flight and not app.flight.done.is_set():
                    app.flight.error = PoolError(definite=True, phase='token')
                    app.flight.done.set()
                idle = list(app.idle); app.idle.clear(); app.created -= len(idle)
                app.condition.notify_all()
            for connection in idle:
                try: connection.close()
                except Exception: pass
