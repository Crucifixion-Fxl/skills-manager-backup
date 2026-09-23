#!/usr/bin/env python3
"""Device Cloud CLI authentication through the existing Casdoor/Feishu login.

The access token is kept in memory.  Only the refresh token is persisted, and
the default credential store uses Windows Credential Manager or the Python
``keyring`` backend on macOS/Linux.  No token is accepted through argv or an
environment variable.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, NamedTuple, Protocol


class AuthenticationError(RuntimeError):
    """Authentication failed without exposing upstream token material."""


class AuthHttpError(AuthenticationError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"authentication endpoint returned HTTP {status_code}")
        self.status_code = status_code


class AuthorizationCode(NamedTuple):
    code: str
    state: str


class AuthenticatedSession(NamedTuple):
    access_token: str
    email: str
    name: str | None


class CredentialStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class JsonTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...


class MemoryCredentialStore:
    """Test adapter. Production callers use :func:`system_credential_store`."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class _ChunkedCredentialStore:
    """Store long secrets as independently protected credential records.

    Windows Credential Manager limits a generic credential blob to 2560
    bytes.  Casdoor refresh tokens can be larger than that, so a small
    manifest is stored under the original key and the token is split across
    generation-specific records.  The digest detects missing or mixed chunks.
    """

    _MANIFEST_PREFIX = "__addx_chunked_v1__:"
    _INVALID_MANIFEST = "secure credential manifest is invalid"
    _CHUNK_CHARS = 1000
    _MAX_CHUNKS = 128

    def __init__(self, backend: CredentialStore) -> None:
        self._backend = backend

    @classmethod
    def _manifest(cls, value: str) -> tuple[str, int, str] | None:
        if not value.startswith(cls._MANIFEST_PREFIX):
            return None
        parts = value[len(cls._MANIFEST_PREFIX):].split(":")
        if len(parts) != 3:
            raise AuthenticationError(cls._INVALID_MANIFEST)
        generation, count_text, digest = parts
        try:
            count = int(count_text)
        except ValueError as error:
            raise AuthenticationError(cls._INVALID_MANIFEST) from error
        if (
            not generation
            or not generation.isascii()
            or not generation.isalnum()
            or count < 1
            or count > cls._MAX_CHUNKS
            or len(digest) != 64
        ):
            raise AuthenticationError(cls._INVALID_MANIFEST)
        return generation, count, digest

    @staticmethod
    def _chunk_key(key: str, generation: str, index: int) -> str:
        return f"{key}.__chunks__.{generation}.{index}"

    def get(self, key: str) -> str | None:
        stored = self._backend.get(key)
        if stored is None:
            return None
        manifest = self._manifest(stored)
        if manifest is None:
            return stored
        generation, count, expected_digest = manifest
        chunks = []
        for index in range(count):
            chunk = self._backend.get(self._chunk_key(key, generation, index))
            if chunk is None:
                raise AuthenticationError("secure credential chunks are incomplete")
            chunks.append(chunk)
        value = "".join(chunks)
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        if not secrets.compare_digest(digest, expected_digest):
            raise AuthenticationError("secure credential chunks failed integrity validation")
        return value

    def set(self, key: str, value: str) -> None:
        old_stored = self._backend.get(key)
        old_manifest = self._manifest(old_stored) if old_stored is not None else None
        if len(value) <= self._CHUNK_CHARS:
            self._backend.set(key, value)
        else:
            chunks = [
                value[offset:offset + self._CHUNK_CHARS]
                for offset in range(0, len(value), self._CHUNK_CHARS)
            ]
            if len(chunks) > self._MAX_CHUNKS:
                raise AuthenticationError("secure credential is too large to persist")
            generation = secrets.token_hex(8)
            written = []
            try:
                for index, chunk in enumerate(chunks):
                    chunk_key = self._chunk_key(key, generation, index)
                    self._backend.set(chunk_key, chunk)
                    written.append(chunk_key)
                digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
                manifest = f"{self._MANIFEST_PREFIX}{generation}:{len(chunks)}:{digest}"
                self._backend.set(key, manifest)
            except Exception:
                for chunk_key in written:
                    self._backend.delete(chunk_key)
                raise
        if old_manifest is not None:
            self._delete_chunks(key, old_manifest)

    def delete(self, key: str) -> None:
        stored = self._backend.get(key)
        try:
            manifest = self._manifest(stored) if stored is not None else None
        except AuthenticationError:
            # The locator itself is unusable. Remove it so authentication can
            # recover through a new interactive login.
            self._backend.delete(key)
            return
        if manifest is not None:
            self._delete_chunks(key, manifest)
        self._backend.delete(key)

    def _delete_chunks(self, key: str, manifest: tuple[str, int, str]) -> None:
        generation, count, _digest = manifest
        first_error: Exception | None = None
        for index in range(count):
            try:
                self._backend.delete(self._chunk_key(key, generation, index))
            except Exception as error:
                first_error = first_error or error
        if first_error is not None:
            raise first_error


class UrllibJsonTransport:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request("GET", url, None, headers)

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", url, body, headers)

    def _request(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None,
    ) -> dict[str, Any]:
        request_headers = {"Accept": "application/json", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise AuthHttpError(error.code) from None
        except urllib.error.URLError as error:
            raise AuthenticationError("authentication endpoint is unavailable") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuthenticationError("authentication endpoint returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise AuthenticationError("authentication endpoint returned a non-object response")
        return payload


class _KeyringCredentialStore:
    def __init__(self, service_name: str) -> None:
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError as error:
            raise AuthenticationError(
                "secure credential storage is unavailable; install the Python keyring package"
            ) from error
        self._keyring = keyring
        self._service_name = service_name

    def get(self, key: str) -> str | None:
        return self._keyring.get_password(self._service_name, key)

    def set(self, key: str, value: str) -> None:
        if platform.system() == "Darwin" and _uses_native_macos_keyring(self._keyring):
            if _update_macos_keyring_password(self._service_name, key, value):
                return
        self._keyring.set_password(self._service_name, key, value)

    def delete(self, key: str) -> None:
        try:
            self._keyring.delete_password(self._service_name, key)
        except self._keyring.errors.PasswordDeleteError:
            pass


def _uses_native_macos_keyring(keyring_module: Any) -> bool:
    backend = keyring_module.get_keyring()
    return backend.__class__.__module__ == "keyring.backends.macOS"


def _update_macos_keyring_password(service: str, username: str, password: str) -> bool:
    """Update an existing macOS Keychain item without replacing its ACL.

    keyring's macOS backend implements ``set_password`` as delete followed by
    add.  Replacing the item also replaces the access-control decision behind
    the user's "Always Allow" choice.  Use Security.framework's update API for
    existing records and let keyring create the record only when it is absent.
    """
    try:
        from keyring.backends.macOS import api  # type: ignore[import-not-found]
    except ImportError as error:
        raise AuthenticationError("macOS Keychain update API is unavailable") from error

    value = password.encode("utf-8")
    value_buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    cf_data_create = api._found.CFDataCreate
    cf_data_create.restype = ctypes.c_void_p
    cf_data_create.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_long,
    )
    sec_item_update = api._sec.SecItemUpdate
    sec_item_update.restype = api.OS_status
    sec_item_update.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    cf_release = api._found.CFRelease
    cf_release.restype = None
    cf_release.argtypes = (ctypes.c_void_p,)
    owned_objects: list[Any] = []
    try:
        secret_data = cf_data_create(None, value_buffer, len(value))
        if not secret_data:
            raise AuthenticationError("macOS Keychain value allocation failed")
        owned_objects.append(secret_data)
        query = api.create_query(
            kSecClass=api.k_("kSecClassGenericPassword"),
            kSecAttrService=service,
            kSecAttrAccount=username,
        )
        owned_objects.append(query)
        update = api.create_query(kSecValueData=ctypes.c_void_p(secret_data))
        owned_objects.append(update)
        status = sec_item_update(query, update)
        if status == api.error.item_not_found:
            return False
        api.Error.raise_for_status(status)
    except api.Error as error:
        raise AuthenticationError("macOS Keychain update failed") from error
    finally:
        for item in reversed(owned_objects):
            cf_release(item)
    return True


if platform.system() == "Windows":
    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]


class _WindowsCredentialStore:
    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    def __init__(self, target: str) -> None:
        if platform.system() != "Windows":
            raise AuthenticationError("Windows Credential Manager is unavailable")
        self._target = target
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]

    def _target_for(self, key: str) -> str:
        return f"{self._target}/{key}"

    def get(self, key: str) -> str | None:
        pointer = ctypes.POINTER(_CREDENTIALW)()
        ok = self._advapi.CredReadW(
            self._target_for(key),
            self._CRED_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        )
        if not ok:
            error = ctypes.get_last_error()
            if error == self._ERROR_NOT_FOUND:
                return None
            raise AuthenticationError("Windows Credential Manager read failed")
        try:
            credential = pointer.contents
            blob = ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
            return blob.decode("utf-16-le")
        finally:
            self._advapi.CredFree(pointer)

    def set(self, key: str, value: str) -> None:
        blob = value.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = _CREDENTIALW()
        credential.Type = self._CRED_TYPE_GENERIC
        credential.TargetName = self._target_for(key)
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "device-cloud-cli"
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            raise AuthenticationError("Windows Credential Manager write failed")

    def delete(self, key: str) -> None:
        ok = self._advapi.CredDeleteW(
            self._target_for(key),
            self._CRED_TYPE_GENERIC,
            0,
        )
        if not ok and ctypes.get_last_error() != self._ERROR_NOT_FOUND:
            raise AuthenticationError("Windows Credential Manager delete failed")


class _InterProcessFileLock:
    """Small cross-platform lock for one complete CLI authentication transaction.

    The file contains no token material.  The lock must cover the credential
    read as well as the refresh/login and rotated credential write; reading a
    refresh token before acquiring this lock would reintroduce the race.
    """

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = 240.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("authentication lock timeout must be positive")
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._file = None
        self._locked = False
        self.was_contended = False

    def __enter__(self) -> "_InterProcessFileLock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("a+b")
            self._file.seek(0, os.SEEK_END)
            if self._file.tell() == 0:
                self._file.write(b"\0")
                self._file.flush()
        except OSError as error:
            self._close()
            raise AuthenticationError("could not create the Device Cloud authentication lock") from error

        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._try_lock()
                self._locked = True
                return self
            except OSError as error:
                self.was_contended = True
                if time.monotonic() >= deadline:
                    self._close()
                    raise AuthenticationError(
                        "timed out waiting for the Device Cloud authentication lock"
                    ) from error
                time.sleep(self.poll_interval_seconds)

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        try:
            if self._locked:
                self._unlock()
        finally:
            self._locked = False
            self._close()

    def _try_lock(self) -> None:
        assert self._file is not None
        self._file.seek(0)
        if platform.system() == "Windows":
            import msvcrt

            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self) -> None:
        assert self._file is not None
        self._file.seek(0)
        if platform.system() == "Windows":
            import msvcrt

            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)

    def _close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def _default_auth_lock_path(server_url: str) -> Path:
    server_key = hashlib.sha256(server_url.rstrip("/").encode("utf-8")).hexdigest()[:24]
    if platform.system() == "Windows" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]) / "AddX" / "DeviceCloud"
    elif os.environ.get("XDG_CACHE_HOME"):
        root = Path(os.environ["XDG_CACHE_HOME"]) / "addx" / "device-cloud"
    else:
        root = Path.home() / ".cache" / "addx" / "device-cloud"
    return root / f"auth-{server_key}.lock"


def server_base_url(graphql_endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(graphql_endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Device Cloud GraphQL endpoint must be an absolute HTTP URL")
    if parsed.path.rstrip("/") != "/graphql":
        raise ValueError("Device Cloud GraphQL endpoint path must be /graphql")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def system_credential_store(server_url: str) -> CredentialStore:
    host = urllib.parse.urlsplit(server_url).netloc.replace(":", "_")
    service = f"addx/device-cloud/{host}"
    if platform.system() == "Windows":
        return _ChunkedCredentialStore(_WindowsCredentialStore(service))
    return _KeyringCredentialStore(service)


def build_authorization_url(config: dict[str, Any], state: str) -> str:
    required = ("authorizationUri", "clientId", "redirectUri", "scope")
    missing = [name for name in required if not str(config.get(name) or "").strip()]
    if missing:
        raise AuthenticationError(
            "CLI authorization configuration is incomplete: " + ", ".join(missing)
        )
    query = urllib.parse.urlencode(
        {
            "client_id": config["clientId"],
            "response_type": "code",
            "redirect_uri": config["redirectUri"],
            "scope": config["scope"],
            "state": state,
        },
        quote_via=urllib.parse.quote,
    )
    separator = "&" if "?" in str(config["authorizationUri"]) else "?"
    return f"{config['authorizationUri']}{separator}{query}"


def _loopback_authorization_code(
    config: dict[str, Any],
    state: str,
) -> AuthorizationCode:
    redirect = urllib.parse.urlsplit(str(config["redirectUri"]))
    if redirect.scheme != "http" or redirect.hostname not in {"127.0.0.1", "localhost"}:
        raise AuthenticationError("CLI redirect URI must use an HTTP loopback host")
    if redirect.port is None:
        raise AuthenticationError("CLI redirect URI must use a fixed port")
    expected_path = redirect.path or "/"
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            callback = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(callback.query)
            if callback.path != expected_path:
                self.send_error(404)
                return
            callback_state = (query.get("state") or [""])[0]
            if not callback_state or not secrets.compare_digest(callback_state, state):
                content = "此登录回调已过期，请关闭此页面。".encode("utf-8")
                self.send_response(409)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            callback_code = (query.get("code") or [""])[0]
            callback_error = (query.get("error") or [""])[0]
            content = "登录结果已返回，可以关闭此页面。".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            result["code"] = callback_code
            result["state"] = callback_state
            result["error"] = callback_error

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = HTTPServer((redirect.hostname, redirect.port), CallbackHandler)
    deadline = time.monotonic() + 180
    try:
        if not webbrowser.open(build_authorization_url(config, state)):
            raise AuthenticationError("could not open the login browser")
        while not result and time.monotonic() < deadline:
            server.timeout = min(0.5, max(0.0, deadline - time.monotonic()))
            server.handle_request()
    finally:
        server.server_close()
    if result.get("error"):
        raise AuthenticationError("Casdoor authorization was rejected")
    if not result.get("code"):
        raise AuthenticationError("Casdoor authorization timed out or returned no code")
    return AuthorizationCode(result["code"], result.get("state", ""))


class DeviceCloudAuthClient:
    _REFRESH_KEY = "refresh_token"

    def __init__(
        self,
        server_url: str,
        *,
        credential_store: CredentialStore | None = None,
        transport: JsonTransport | None = None,
        authorization_code_provider: Callable[
            [dict[str, Any], str], AuthorizationCode
        ] | None = None,
        auth_lock_path: Path | None = None,
        auth_lock_timeout_seconds: float = 240.0,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.credential_store = credential_store or system_credential_store(self.server_url)
        self.transport = transport or UrllibJsonTransport()
        self.authorization_code_provider = (
            authorization_code_provider or _loopback_authorization_code
        )
        self.auth_lock_path = auth_lock_path or _default_auth_lock_path(self.server_url)
        self.auth_lock_timeout_seconds = auth_lock_timeout_seconds
        self.session: AuthenticatedSession | None = None

    @property
    def access_token(self) -> str:
        if self.session is None:
            raise AuthenticationError("Device Cloud authentication has not completed")
        return self.session.access_token

    def authenticate(self) -> AuthenticatedSession:
        lock = self._authentication_lock()
        with lock:
            return self._authenticate_locked(
                allow_interactive=not lock.was_contended,
            )

    def refresh(self) -> AuthenticatedSession:
        return self.authenticate()

    def _authentication_lock(self) -> _InterProcessFileLock:
        return _InterProcessFileLock(
            self.auth_lock_path,
            timeout_seconds=self.auth_lock_timeout_seconds,
        )

    def _authenticate_locked(self, *, allow_interactive: bool) -> AuthenticatedSession:
        refresh_token = self._load_refresh_token()
        if not refresh_token:
            if not allow_interactive:
                raise AuthenticationError(
                    "the preceding Device Cloud login did not complete; "
                    "refusing to open another login browser"
                )
            return self._interactive_login()

        # One retry supports migration from older clients that may still write
        # the shared credential without taking this lock.  Cooperative clients
        # never enter this branch because they read only after acquiring it.
        for _attempt in range(2):
            try:
                tokens = self.transport.post_json(
                    f"{self.server_url}/auth/cli/refresh",
                    {"refreshToken": refresh_token},
                )
                session = self._accept_tokens(
                    tokens,
                    require_refresh=False,
                    previous_refresh_token=refresh_token,
                )
                print("SSO: reused cached refresh token", file=sys.stderr, flush=True)
                return session
            except AuthHttpError as error:
                if error.status_code not in {400, 401}:
                    raise
                latest_refresh_token = self._load_refresh_token()
                if (
                    latest_refresh_token
                    and not secrets.compare_digest(latest_refresh_token, refresh_token)
                ):
                    refresh_token = latest_refresh_token
                    continue
                self._delete_refresh_token_if_unchanged(refresh_token)
                if not allow_interactive:
                    raise AuthenticationError(
                        "the preceding Device Cloud login did not produce a reusable session; "
                        "refusing to open another login browser"
                    )
                return self._interactive_login()
        raise AuthenticationError("refresh token changed repeatedly during authentication")

    def _delete_refresh_token_if_unchanged(self, expected: str) -> None:
        current = self._load_refresh_token()
        if current is not None and secrets.compare_digest(current, expected):
            self.credential_store.delete(self._REFRESH_KEY)

    def _load_refresh_token(self) -> str | None:
        try:
            return self.credential_store.get(self._REFRESH_KEY)
        except AuthenticationError:
            self.credential_store.delete(self._REFRESH_KEY)
            return None

    def _interactive_login(self) -> AuthenticatedSession:
        config = self.transport.get_json(f"{self.server_url}/auth/cli/config")
        print(
            f"SSO: browser launched at {time.strftime('%H:%M:%S')}",
            file=sys.stderr,
            flush=True,
        )
        state = secrets.token_urlsafe(32)
        authorization = self.authorization_code_provider(config, state)
        if not secrets.compare_digest(authorization.state, state):
            raise AuthenticationError("OAuth state mismatch")
        tokens = self.transport.post_json(
            f"{self.server_url}/auth/cli/token",
            {
                "code": authorization.code,
                "redirectUri": config["redirectUri"],
            },
        )
        return self._accept_tokens(tokens, require_refresh=True)

    def _accept_tokens(
        self,
        tokens: dict[str, Any],
        *,
        require_refresh: bool,
        previous_refresh_token: str | None = None,
    ) -> AuthenticatedSession:
        access_token = str(tokens.get("access_token") or "").strip()
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        if not access_token or (require_refresh and not refresh_token):
            raise AuthenticationError("Casdoor token response is incomplete")
        refresh_token_changed = refresh_token and (
            previous_refresh_token is None
            or not secrets.compare_digest(refresh_token, previous_refresh_token)
        )
        if refresh_token_changed:
            self.credential_store.set(self._REFRESH_KEY, refresh_token)
        user = self.transport.get_json(
            f"{self.server_url}/auth/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        email = str(user.get("email") or "").strip()
        if not email:
            raise AuthenticationError("authenticated Device Cloud user has no email")
        self.session = AuthenticatedSession(
            access_token=access_token,
            email=email,
            name=str(user.get("name") or "").strip() or None,
        )
        return self.session


__all__ = [
    "AuthenticatedSession",
    "AuthenticationError",
    "AuthorizationCode",
    "DeviceCloudAuthClient",
    "MemoryCredentialStore",
    "build_authorization_url",
    "server_base_url",
]
