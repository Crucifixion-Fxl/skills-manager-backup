"""Whole-inventory proof and bounded, identity-checked readiness for installation."""
import math
import time
import uuid

from agent_recovery import _validate
from recovery_controller import (_agent_env_matches, load_snapshots, process_live,
                                 read_env, systemd_main_pid, validate_config)
import recovery_inventory
from recovery_runtime import request_prewarm, verify_process


REMEDIATION = {
    "recovery_runtime_unverified": "该 Agent 的实际进程、运行版本或恢复记录无法确认；请核对该 Agent 的启动日志与发布版本，保留任务记录后修复。",
    "recovery_provider_not_ready": "该 Agent 的模型服务在限定时间内未就绪；请检查模型登录、网络和启动日志。未将本次安装标为成功，未重启 Agent 或代发任务。",
    "recovery_prewarm_unverified": "该 Agent 的预热信号未能安全送达当前进程；请检查服务是否正在重启及本机进程权限，未继续安装。",
}


class RuntimeProofError(ValueError):
    def __init__(self, agent, code="recovery_runtime_unverified"):
        super().__init__(code)
        self.agent, self.code = agent, code

    def public(self):
        return dict(error=self.code, agent=self.agent, installed=False,
                    remediation=REMEDIATION[self.code])


def _inspect(config, main_pid, phases):
    """Read-only full inventory; do not signal a partially verified inventory."""
    validate_config(config)
    proofs = []
    for agent in config["agents"]:
        try:
            snapshots = load_snapshots(agent["journal_dir"])
            for snapshot in snapshots:
                _validate(snapshot, agent["pubkey"], config["relay_url"])
                recovery_inventory.validate(snapshot)
            live = [s for s in snapshots if process_live(s)]
            pid = main_pid(agent["unit"])
            current = live[0] if len(live) == 1 and live[0]["pid"] == pid else None
            if (current is None or current.get("phase") not in phases
                    or current["runtime_policy"]["owner"] != config["owner_pubkey"]
                    or not _agent_env_matches(agent, config)
                    or not verify_process(snapshot=current, main_pid=pid,
                                          binary_sha256=agent["binary_sha256"], revision=agent["revision"])):
                raise ValueError("actual process unverified")
            recovery_inventory.pending(snapshots, current["generation"])
            channels = current.get("channels")
            if (not isinstance(channels, list) or len(channels) > 4096
                    or any(not isinstance(channel, str) or str(uuid.UUID(channel)) != channel for channel in channels)
                    or len(set(channels)) != len(channels)):
                raise ValueError("invalid actual subscriptions")
            env = read_env(agent["env_file"])
            if "BUZZ_ACP_CHANNELS" in env:
                expected = [channel.strip() for channel in env["BUZZ_ACP_CHANNELS"].split(",")]
                if (any(str(uuid.UUID(channel)) != channel for channel in expected)
                        or len(set(expected)) != len(expected) or set(channels) != set(expected)):
                    raise ValueError("actual subscriptions differ from approved channels")
            if (main_pid(agent["unit"]) != pid or not verify_process(snapshot=current, main_pid=pid,
                    binary_sha256=agent["binary_sha256"], revision=agent["revision"])):
                raise ValueError("runtime changed during installation proof")
            proof = dict(name=agent["name"], generation=current["generation"],
                         binary_sha256=agent["binary_sha256"], revision=agent["revision"])
            proofs.append((agent, current, proof))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RuntimeProofError(agent["name"]) from error
    return proofs


def verify_inventory(config, *, main_pid=systemd_main_pid):
    """Read-only ready proof. No partial PASS or configuration-only evidence."""
    return [proof for _agent, _current, proof in _inspect(config, main_pid, {"ready"})]


def inspect_inventory(config, *, main_pid=systemd_main_pid):
    """Read-only preflight for --check: every Agent verified, no prewarm signal."""
    return [dict(name=agent["name"], phase=current["phase"])
            for agent, current, _proof in _inspect(config, main_pid, {"ready", "starting"})]


def _birth(snapshot):
    return tuple(snapshot[key] for key in ("generation", "boot_id", "pid", "process_start_ticks"))


def ensure_ready(config, *, main_pid=systemd_main_pid, timeout=60):
    """Prewarm each verified idle generation once; never restart or submit work.

    All Agents, historical responsibility and approved subscriptions must pass
    before any signal. A signal ACK does not count as ready. Poll the real full
    inventory and reject generation changes rather than following a new PID.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("invalid readiness bound")
    deadline = time.monotonic() + timeout
    rows = _inspect(config, main_pid, {"ready", "starting"})
    generations = {agent["name"]: _birth(current) for agent, current, _proof in rows}
    for agent, current, _proof in rows:
        if current["phase"] == "starting":
            if time.monotonic() >= deadline:
                raise RuntimeProofError(agent["name"], "recovery_provider_not_ready")
            if not request_prewarm(snapshot=current, main_pid=lambda: main_pid(agent["unit"]),
                                   binary_sha256=agent["binary_sha256"], revision=agent["revision"]):
                raise RuntimeProofError(agent["name"], "recovery_prewarm_unverified")
    while True:
        rows = _inspect(config, main_pid, {"ready", "starting"})
        for agent, current, _proof in rows:
            if _birth(current) != generations[agent["name"]]:
                raise RuntimeProofError(agent["name"])
        pending = [agent["name"] for agent, current, _proof in rows if current["phase"] != "ready"]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeProofError(pending[0] if pending else "inventory", "recovery_provider_not_ready")
        if not pending:
            return [proof for _agent, _current, proof in rows]
        time.sleep(min(0.25, remaining))
