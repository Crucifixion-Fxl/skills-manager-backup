"""Harness profiles: one runnable combination = adapter + wrapper + account home + model + effort.

Defaults live in assets/profiles.default.json; ~/.config/buzz/harness-profiles.json (or --profiles)
overrides by id and may add more accounts of the same harness.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import asset_path

HARNESSES = ("grok", "claude", "codex")
EFFORTS = ("default", "low", "medium", "high", "xhigh", "max", "ultra")


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Profile:
    id: str
    harness: str
    model: str
    effort: str
    priority: int
    wrapper: str | None = None  # claude family: CLAUDE_CODE_EXECUTABLE target
    home: str | None = None  # account home: CLAUDE_CONFIG_DIR / CODEX_HOME / ~/.grok
    provider: str | None = None  # e.g. "glm" — picks the error-signature family
    enabled: bool = True
    user_home: str = field(default="", compare=False)  # $HOME the paths were expanded against
    command_override: str | None = None
    media_proxy: str | None = None
    media_buzz_cli: str | None = None

    @property
    def command(self) -> str:
        if self.command_override:
            return self.command_override
        agents = os.path.join(self.user_home, ".local/lib/buzz-agents/node_modules/.bin")
        return {
            "grok": os.path.join(self.user_home, ".config/buzz/agents/grok-buzz-acp.sh"),
            "claude": os.path.join(agents, "claude-agent-acp"),
            "codex": os.path.join(agents, "codex-acp"),
        }[self.harness]

    @property
    def launch_command(self) -> str:
        return self.media_proxy or self.command

    def env_updates(self) -> dict[str, str | None]:
        """Variables to write into an agent env file. None = remove the variable."""
        return {
            "BUZZ_ACP_AGENT_COMMAND": self.launch_command,
            "BUZZ_ACP_AGENT_ARGS": "",
            "BUZZ_ACP_MODEL": self.model,
            "BUZZ_ACP_EFFORT_LEVEL": self.effort,
            "HARNESS_CLAUDE_WRAPPER": self.wrapper if self.harness == "claude" else None,
            "CODEX_HOME": self.home if self.harness == "codex" else None,
            "CLAUDE_CODE_EXECUTABLE": self.wrapper if self.harness == "claude" else None,
            "CLAUDE_CONFIG_DIR": self.home if self.harness == "claude" else None,
            "BUZZ_ACP_MEDIA_ADAPTER_COMMAND": self.command if self.media_proxy else None,
            "BUZZ_ACP_MEDIA_BUZZ_CLI": self.media_buzz_cli if self.media_proxy else None,
            "BUZZ_ACP_MEDIA_MODE": None if self.media_proxy else "stock_text_only",
        }


def _expand(path: str | None, home: str) -> str | None:
    if not path:
        return None
    return home + path[1:] if path.startswith("~") else path


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _text(raw: dict, key: str, required: bool = False):
    """A profile value ends up in a file that bash sources: it must be a plain single-line string."""
    v = raw.get(key)
    if v is None or v == "":
        if required:
            raise ProfileError(f"{key} is required")
        return None
    if not isinstance(v, str) or _CTRL.search(v) or v != v.strip():
        raise ProfileError(f"{key} must be a plain single-line string without control characters")
    return v


def _build(raw: dict, home: str) -> Profile:
    for key in ("id", "harness", "model", "effort", "wrapper", "home", "provider", "command",
                "media_proxy", "media_buzz_cli"):
        _text(raw, key)
    pid = str(raw.get("id") or "").strip()
    if not pid:
        raise ProfileError("profile id is required")
    if raw.get("harness") not in HARNESSES:
        raise ProfileError(f"{pid}: harness must be one of {HARNESSES}")
    if not str(raw.get("model") or "").strip():
        raise ProfileError(f"{pid}: model is required")
    if raw.get("effort") not in EFFORTS:
        raise ProfileError(f"{pid}: effort must be one of {EFFORTS}")
    command = _expand(raw.get("command"), home)
    media_proxy = _expand(raw.get("media_proxy"), home)
    media_buzz_cli = _expand(raw.get("media_buzz_cli"), home)
    for key, value in (("command", command), ("media_proxy", media_proxy), ("media_buzz_cli", media_buzz_cli)):
        if value is not None and not os.path.isabs(value):
            raise ProfileError(f"{pid}: {key} must be an absolute path (or start with ~)")
    if bool(media_proxy) != bool(media_buzz_cli):
        raise ProfileError(f"{pid}: media_proxy and media_buzz_cli must be configured together")
    default_command = command or {
        "grok": os.path.join(home, ".config/buzz/agents/grok-buzz-acp.sh"),
        "claude": os.path.join(home, ".local/lib/buzz-agents/node_modules/.bin/claude-agent-acp"),
        "codex": os.path.join(home, ".local/lib/buzz-agents/node_modules/.bin/codex-acp"),
    }[raw["harness"]]
    if media_proxy and Path(media_proxy).name != Path(default_command).name:
        raise ProfileError(f"{pid}: media_proxy basename must match the adapter command basename")
    return Profile(
        id=pid, harness=raw["harness"], model=raw["model"], effort=raw["effort"],
        priority=int(raw.get("priority", 100)), wrapper=_expand(raw.get("wrapper"), home),
        home=_expand(raw.get("home"), home), provider=raw.get("provider"),
        enabled=bool(raw.get("enabled", True)), user_home=home, command_override=command,
        media_proxy=media_proxy, media_buzz_cli=media_buzz_cli,
    )


def load_profiles(home: str, user_file: str | None = None) -> list[Profile]:
    merged: dict[str, dict] = {}
    for p in json.load(open(asset_path("profiles.default.json")))["profiles"]:
        merged[p["id"]] = dict(p)
    if user_file and os.path.exists(user_file):
        st = os.stat(user_file)
        if st.st_uid != os.getuid() or st.st_mode & 0o002:
            raise ProfileError("profiles file must be owned by you and not world-writable")
        seen: set[str] = set()
        for p in json.load(open(user_file)).get("profiles", []):
            pid = str(p.get("id") or "")
            if pid in seen:
                raise ProfileError(f"duplicate profile id in user file: {pid}")
            seen.add(pid)
            merged[pid] = {**merged.get(pid, {}), **p}
    built = [_build(raw, home) for raw in merged.values()]
    return sorted((p for p in built if p.enabled), key=lambda p: (p.priority, p.id))


def identify(env_vars: dict, profiles: list[Profile]) -> str | None:
    """Reverse lookup: which profile do an env file's harness variables describe?"""
    cmd = env_vars.get("BUZZ_ACP_AGENT_COMMAND")
    for p in profiles:
        if cmd != p.launch_command:
            continue
        if p.media_proxy and (
            env_vars.get("BUZZ_ACP_MEDIA_ADAPTER_COMMAND") != p.command
            or env_vars.get("BUZZ_ACP_MEDIA_BUZZ_CLI") != p.media_buzz_cli
            or env_vars.get("BUZZ_ACP_MEDIA_MODE") not in (None, "")
        ):
            continue
        if not p.media_proxy and (
            env_vars.get("BUZZ_ACP_MEDIA_ADAPTER_COMMAND") not in (None, "")
            or env_vars.get("BUZZ_ACP_MEDIA_BUZZ_CLI") not in (None, "")
            or env_vars.get("BUZZ_ACP_MEDIA_MODE") != "stock_text_only"
        ):
            continue
        if p.harness == "claude" and env_vars.get("HARNESS_CLAUDE_WRAPPER") != p.wrapper:
            continue
        if p.harness == "claude" and env_vars.get("CLAUDE_CONFIG_DIR") not in (None, p.home):
            continue
        if p.harness == "codex" and env_vars.get("CODEX_HOME") != p.home:
            continue
        return p.id
    return None
