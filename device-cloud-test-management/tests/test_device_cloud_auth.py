"""Regression tests for Device Cloud secure credential persistence."""

from __future__ import annotations

import importlib.util
import sys
import types
import ctypes
from pathlib import Path
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "device_cloud_auth.py"
SPEC = importlib.util.spec_from_file_location("device_cloud_auth_contract", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
AUTH = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUTH
SPEC.loader.exec_module(AUTH)


def _store(monkeypatch, update_result: bool, *, native_macos_backend: bool = True):
    writes: list[tuple[str, str, str]] = []
    backend_type = type("Keyring", (), {})
    backend_type.__module__ = (
        "keyring.backends.macOS" if native_macos_backend else "custom.keyring"
    )
    fake_keyring = SimpleNamespace(
        get_password=lambda *_args: None,
        set_password=lambda *args: writes.append(args),
        get_keyring=lambda: backend_type(),
        errors=SimpleNamespace(PasswordDeleteError=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    monkeypatch.setattr(AUTH.platform, "system", lambda: "Darwin")
    updates: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        AUTH,
        "_update_macos_keyring_password",
        lambda *args: updates.append(args) or update_result,
    )
    return AUTH._KeyringCredentialStore("service"), updates, writes


def test_existing_macos_keychain_item_is_updated_without_replacement(monkeypatch) -> None:
    store, updates, writes = _store(monkeypatch, True)

    store.set("refresh_token", "rotated")

    assert updates == [("service", "refresh_token", "rotated")]
    assert writes == []


def test_missing_macos_keychain_item_is_created_through_keyring(monkeypatch) -> None:
    store, updates, writes = _store(monkeypatch, False)

    store.set("refresh_token", "initial")

    assert updates == [("service", "refresh_token", "initial")]
    assert writes == [("service", "refresh_token", "initial")]


def test_custom_macos_keyring_backend_is_not_bypassed(monkeypatch) -> None:
    store, updates, writes = _store(
        monkeypatch,
        True,
        native_macos_backend=False,
    )

    store.set("refresh_token", "rotated")

    assert updates == []
    assert writes == [("service", "refresh_token", "rotated")]


class _RecordingCredentialStore:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def get(self, _key: str) -> str | None:
        return None

    def set(self, key: str, value: str) -> None:
        self.writes.append((key, value))

    def delete(self, _key: str) -> None:
        return None


def _accept_refresh(previous: str, returned: str) -> list[tuple[str, str]]:
    store = _RecordingCredentialStore()
    transport = SimpleNamespace(
        get_json=lambda *_args, **_kwargs: {
            "email": "tester@example.com",
            "name": "Tester",
        }
    )
    client = AUTH.DeviceCloudAuthClient(
        "https://device-cloud.example",
        credential_store=store,
        transport=transport,
    )

    client._accept_tokens(
        {"access_token": "access", "refresh_token": returned},
        require_refresh=False,
        previous_refresh_token=previous,
    )
    return store.writes


def test_unchanged_refresh_token_is_not_written() -> None:
    assert _accept_refresh("same", "same") == []


def test_rotated_refresh_token_is_written_once() -> None:
    assert _accept_refresh("old", "new") == [("refresh_token", "new")]


class _FakeCFunction:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self.callback(*args)


def _fake_macos_api(monkeypatch, status: int):
    released: list[int] = []
    created = iter((101, 102, 103))

    class ApiError(Exception):
        @classmethod
        def raise_for_status(cls, value: int) -> None:
            if value:
                raise cls(value)

    api = SimpleNamespace(
        _found=SimpleNamespace(
            CFDataCreate=_FakeCFunction(lambda *_args: next(created)),
            CFRelease=_FakeCFunction(lambda value: released.append(value)),
        ),
        _sec=SimpleNamespace(SecItemUpdate=_FakeCFunction(lambda *_args: status)),
        OS_status=ctypes.c_int32,
        error=SimpleNamespace(item_not_found=-25300),
        Error=ApiError,
        k_=lambda name: name,
        create_query=lambda **_kwargs: next(created),
    )
    macos_module = types.ModuleType("keyring.backends.macOS")
    macos_module.api = api
    monkeypatch.setitem(sys.modules, "keyring.backends.macOS", macos_module)
    return released


def test_macos_update_releases_all_created_objects(monkeypatch) -> None:
    released = _fake_macos_api(monkeypatch, 0)

    assert AUTH._update_macos_keyring_password("service", "user", "value") is True
    assert released == [103, 102, 101]


def test_macos_missing_item_releases_objects_before_create_fallback(monkeypatch) -> None:
    released = _fake_macos_api(monkeypatch, -25300)

    assert AUTH._update_macos_keyring_password("service", "user", "value") is False
    assert released == [103, 102, 101]


def test_macos_update_error_releases_objects(monkeypatch) -> None:
    released = _fake_macos_api(monkeypatch, -128)

    try:
        AUTH._update_macos_keyring_password("service", "user", "value")
    except AUTH.AuthenticationError:
        pass
    else:
        raise AssertionError("Keychain errors must fail closed")
    assert released == [103, 102, 101]
