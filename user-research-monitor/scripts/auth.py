"""OAuth flow + token refresh for Google Forms API.

首次跑会弹浏览器授权；之后用 refresh token 自动续期。
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_oauth_config() -> dict:
    with open(CONFIG_DIR / "google_oauth.yaml", "r") as f:
        return yaml.safe_load(f)


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path))


def get_credentials() -> Credentials:
    """Return valid Credentials. Refresh or run OAuth flow as needed."""
    cfg = load_oauth_config()
    scopes = cfg["scopes"]
    client_secret = _expand(cfg["client_secret_path"])
    token_path = _expand(cfg["token_path"])
    port = cfg.get("local_redirect_port", 0)

    if not client_secret.exists():
        raise FileNotFoundError(
            f"OAuth client_secret.json not found at {client_secret}.\n"
            f"Phase 0 未完成。请在本机创建 skill-local config/google_oauth.yaml，并指向本机 OAuth client secret。"
        )

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                # refresh 失败（如 token 被吊销），回退到完整 flow
                print(f"⚠️  Token refresh failed ({e}). Re-running OAuth flow…")
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secret), scopes
            )
            creds = flow.run_local_server(port=port, open_browser=True)
        # 写回 token
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_fd = os.open(
            token_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(token_fd, "w") as f:
            f.write(creds.to_json())
        token_path.chmod(0o600)
        print(f"✓ Token saved to {token_path}")

    return creds


if __name__ == "__main__":
    creds = get_credentials()
    print(f"✓ Authenticated. Scopes: {creds.scopes}")
