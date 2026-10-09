"""What this machine runs: the bindings found in the sync config root and the Feishu app that receives each one's events.

A binding is one `<root>/<name>/config.json` (the same file the timers used). Its sync app is the app of its
`desk_pubkey` agent: the bot that sends people's words to the group and so the one that sees the group's events
(ADR-0026). One app can serve several bindings; it still gets one long connection (ADR-0025, P0-2).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .safety import read_owned, notice, SafetyError
except ImportError:
    from safety import read_owned, notice, SafetyError

DEFAULT_ROOT = Path.home() / ".config" / "buzz-feishu-sync"


@dataclass(frozen=True)
class Binding:
    name: str
    config: Path
    state_dir: Path
    channel_id: str
    chat_id: str
    sync_app_id: str
    lark_config_dir: str
    lark_data_dir: str
    relay_url: str


@dataclass
class Registry:
    bindings: dict[str, Binding] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)  # name -> plain reason

    def by_app(self) -> dict[str, list[Binding]]:
        apps: dict[str, list[Binding]] = {}
        for b in self.bindings.values():
            peers = apps.get(b.sync_app_id, [])
            if peers and _profile(peers[0]) != _profile(b):
                raise ValueError(notice("duplicate"))
            apps.setdefault(b.sync_app_id, []).append(b)
        return apps

    def by_chat(self) -> dict[str, Binding]:
        return self._unique_index("chat_id")

    def by_channel(self) -> dict[str, Binding]:
        return self._unique_index("channel_id")

    def _unique_index(self, key: str) -> dict[str, Binding]:
        result = {}
        for binding in self.bindings.values():
            value = getattr(binding, key)
            if value in result:
                raise ValueError(notice("duplicate"))
            result[value] = binding
        return result


def _profile(b: Binding) -> tuple[str, str]:
    return os.path.abspath(b.lark_config_dir), os.path.abspath(b.lark_data_dir)


def _relay_url(env_file: str) -> str:
    for line in read_owned(env_file).decode().splitlines():
        if line.startswith("BUZZ_RELAY_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SafetyError(notice("config"))


def load(root: Path = DEFAULT_ROOT, only: set[str] | None = None) -> Registry:
    reg = Registry()
    for cfg_path in sorted(root.glob("*/config.json")):
        name = cfg_path.parent.name
        if only is not None and name not in only:
            continue
        try:
            try:
                cfg = json.loads(read_owned(cfg_path))
            except SafetyError:
                reg.skipped[name] = notice("permissions")
                continue
            if not isinstance(cfg, dict):
                raise ValueError
            if not cfg.get("chat_id"):
                reg.skipped[name] = notice("chat")
                continue
            desk, agents = cfg.get("desk_pubkey"), cfg.get("agents")
            if not isinstance(desk, str) or not isinstance(agents, dict) or desk not in agents:
                reg.skipped[name] = notice("bot")
                continue
            agent = agents[desk]
            if not isinstance(agent, dict):
                raise ValueError
            values = [cfg[k] for k in ("channel_id", "chat_id", "mirror_env_file")]
            values += [agent[k] for k in ("app_id", "lark_config_dir", "lark_data_dir")]
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise ValueError
            reg.bindings[name] = Binding(
                name=name, config=cfg_path, state_dir=cfg_path.parent / "state",
                channel_id=cfg["channel_id"], chat_id=cfg["chat_id"], sync_app_id=agent["app_id"],
                lark_config_dir=agent["lark_config_dir"], lark_data_dir=agent["lark_data_dir"],
                relay_url=_relay_url(cfg["mirror_env_file"]))
        except (KeyError, TypeError, ValueError, OSError, RecursionError):
            reg.skipped[name] = notice("config")
    # No arbitrary winner: every participant in a conflicting group fails closed.
    conflicts = set()
    for key in ("chat_id", "channel_id", "sync_app_id"):
        groups: dict[str, list[Binding]] = {}
        for binding in reg.bindings.values():
            groups.setdefault(getattr(binding, key), []).append(binding)
        for group in groups.values():
            if len(group) > 1 and (key != "sync_app_id" or len({_profile(b) for b in group}) > 1):
                conflicts.update(b.name for b in group)
    for name in conflicts:
        del reg.bindings[name]
        reg.skipped[name] = notice("duplicate")
    return reg
