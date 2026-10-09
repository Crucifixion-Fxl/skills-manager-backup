"""Access only the isolated credentials and CLI profiles for hostd test bots."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any

TEST_BOTS = ("hostd-test-desk", "hostd-test-a", "hostd-test-b")
NODE = "/home/jchen/.nvm/versions/node/v20.20.1/bin/node"
CLI_ENTRY = "/home/jchen/.npm-global/lib/node_modules/@larksuite/cli/scripts/run.js"


def profile_dir(name: str) -> Path:
    if name not in TEST_BOTS:
        raise SystemExit("Probe access refused: only hostd test bots are allowed")
    return Path.home() / ".config" / "lark-agents" / name


def profile_env(name: str) -> dict[str, str]:
    d = profile_dir(name)
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")}
    env.update(LARKSUITE_CLI_CONFIG_DIR=str(d / "config"), LARKSUITE_CLI_DATA_DIR=str(d / "data"),
               LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1", TZ="UTC")
    return env



def _normalized_error(parsed: dict, message: str) -> dict:
    source_error = parsed.get("error") or {}
    if not isinstance(source_error, dict):
        source_error = {}
    code = parsed.get("code", source_error.get("code"))
    if not isinstance(code, int) or isinstance(code, bool):
        code = None
    url = source_error.get("console_url") or parsed.get("console_url")
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
        port = parts.port if parts else None
    except ValueError:
        parts, port = None, None
    valid_url = None
    if (parts and parts.scheme == "https" and parts.hostname == "open.feishu.cn" and
            port in (None, 443) and not parts.username and not parts.password and
            parts.path == "/page/scope-apply"):
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
                           if k in {"clientID", "addons"} and len(v) <= 256])
        valid_url = urlunsplit(("https", "open.feishu.cn", parts.path, query, ""))
    scopes = source_error.get("missing_scopes") or parsed.get("missing_scopes") or []
    if not isinstance(scopes, list):
        scopes = []
    scopes = [scope for scope in scopes if isinstance(scope, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", scope)]
    return {"ok": False, "code": code,
            "error": {"code": code, "console_url": valid_url, "missing_scopes": scopes, "message": message},
            "remedy": "Open the supplied Feishu permission link, grant the listed test-bot scope, and rerun the probe.",
            "copy_to_ai": "Help me resolve this hostd test-bot permission issue using the scope names and validated permission link; do not display credentials or raw server errors."}


def lark(name: str, *args: str, timeout: float = 60) -> dict:
    """Run as bot through the test bot's isolated config/data directories; suppress CLI errors."""
    profile_dir(name)
    if not args:
        return {"ok": False, "error": {"message": "Probe command is empty"}}
    command_args = list(args)
    if "--profile" in command_args:
        return {"ok": False, "error": {"message": "Probe command refused: use the isolated test-bot environment"}}
    if "--as" in command_args:
        try:
            identity = command_args[command_args.index("--as") + 1]
        except IndexError:
            return {"ok": False, "error": {"message": "Probe command refused: identity is incomplete"}}
        if identity != "bot":
            return {"ok": False, "error": {"message": "Probe command refused: only the test bot identity is allowed"}}
    else:
        command_args.extend(("--as", "bot"))
    try:
        result = subprocess.run([NODE, CLI_ENTRY, *command_args], env=profile_env(name), capture_output=True,
                                text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": {"message": "Feishu test-bot command failed; verify the test-bot setup and retry"},
                "remedy": "Verify the isolated test-bot files and repeat the read-only probe.",
                "copy_to_ai": "Help me check the isolated hostd test-bot setup and rerun this probe without printing credentials."}
    parsed = {}
    for candidate in (result.stdout, result.stderr if result.returncode != 0 else ""):
        try:
            parsed = json.loads(candidate or "")
            if isinstance(parsed, dict):
                break
        except json.JSONDecodeError:
            parsed = {}
    api_code = parsed.get("code")
    if result.returncode != 0 or parsed.get("ok") is False or (isinstance(api_code, int) and api_code != 0):
        return _normalized_error(parsed, "Feishu test-bot command failed; check its required permission and retry")
    output = result.stdout or ""
    try:
        start = output.find("{")
        if start < 0:
            raise ValueError("not JSON")
        parsed = json.loads(output[start:])
        return parsed if isinstance(parsed, dict) else {"ok": False, "error": {"message": "Unexpected command result"}}
    except (json.JSONDecodeError, ValueError):
        # CLI diagnostics can contain profile paths and sensitive server responses.
        return {"ok": False, "error": {"message": "Feishu test-bot command returned an unreadable response"},
                "remedy": "Check the test-bot CLI setup and repeat the probe.",
                "copy_to_ai": "Help me check the hostd test-bot CLI setup without printing raw diagnostics or credentials."}


def app_credentials(name: str) -> tuple[str, str]:
    """Return (app_id, app_secret), decrypting only through hostd's safe credentials helper."""
    d = profile_dir(name)
    config_dir, data_dir = d / "config", d / "data"
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from hostd.safety import read_owned
        config = json.loads(read_owned(config_dir / "config.json").decode("utf-8"))
        apps = config.get("apps") or []
        app_id = apps[0].get("appId") if apps else None
        if not isinstance(app_id, str) or not app_id:
            raise ValueError("missing app id")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        raise SystemExit("Probe credentials unavailable: verify the declared test-bot profile") from None
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys_path = str(scripts)
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    try:
        from hostd.secrets_store import app_secret
        secret = app_secret(app_id, str(config_dir), str(data_dir))
    except SystemExit:
        raise
    except Exception:
        raise SystemExit("Probe credentials unavailable: verify the test-bot credential store") from None
    return app_id, secret
