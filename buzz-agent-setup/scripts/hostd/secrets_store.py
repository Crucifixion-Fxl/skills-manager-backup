"""Read a Feishu app secret from its lark-cli profile, in memory only (PO 2026-10-04: decrypt the lark-cli store).

lark-cli 1.0.85 keeps it as AES-256-GCM: <data>/lark-cli/appsecret_<app_id>.enc = 12-byte nonce + ciphertext+tag,
key = <data>/lark-cli/master.key. The secret is never written, logged or passed in argv.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from .safety import read_owned, notice
except ImportError:
    from safety import read_owned, notice


def app_secret(app_id: str, config_dir: str, data_dir: str) -> str:
    if not isinstance(app_id, str) or re.fullmatch(r"cli_[A-Za-z0-9]+", app_id) is None:
        raise SystemExit(notice("secret"))
    try:
        doc = json.loads(read_owned(Path(config_dir) / "config.json"))
        apps = doc.get("apps") if isinstance(doc, dict) else None
        if not isinstance(apps, list):
            raise ValueError
        if not any(isinstance(a, dict) and a.get("appId") == app_id for a in apps):
            raise SystemExit(notice("profile"))
        store = Path(data_dir) / "lark-cli"
        key = read_owned(store / "master.key", max_bytes=32)
        blob = read_owned(store / f"appsecret_{app_id}.enc")
        if len(key) != 32 or len(blob) <= 28:
            raise ValueError
        secret = AESGCM(key).decrypt(blob[:12], blob[12:], None).decode()
        if not secret:
            raise ValueError
        return secret
    except Exception:  # crypto exceptions can carry input; only fixed notice leaves this boundary
        raise SystemExit(notice("secret")) from None
