#!/usr/bin/env python3
"""Generic owner recovery round; suitable for a user service/timer or CLI.

Never restarts processes, changes membership, approves requests, or runs model
code. Only runtime-journalled work in verified original Threads may be resumed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import uuid

# The service invokes python -I: only this immutable release supplies imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_recovery import JOURNAL_VERSION, RecoveryStore, recover
from audit_local_alignment import run_bounded_text
from recovery_relay import RecoveryRelay, canonical_origin, wire
from recovery_runtime import _identity, verify_process, request_prewarm
from recovery_schedule import RecoverySchedule
from recovery_tick import begin_tick, complete_tick

HEX64 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}")


def storage_failure(error):
    """Fixed public diagnostics; SQL text/paths and exception strings stay private.

    An ACK commit may fail after publication. Never claim the work did not run,
    reset the outbox, or sign an unjournalled emergency notice on this path.
    """
    number = getattr(error, "sqlite_errorcode", None)
    primary = number & 0xff if type(number) is int else None
    if primary == sqlite3.SQLITE_FULL:
        code, reason = "recovery_storage_capacity_exceeded", "恢复状态存储容量不足"
        action = "检查磁盘空间与数据库容量限制"
    elif primary in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        code, reason = "recovery_storage_invalid", "恢复状态数据库无法完整读取"
        action = "核对数据库与备份，修复后再恢复调度，不要清空重建"
    elif primary in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        code, reason = "recovery_storage_busy", "恢复状态数据库正被占用"
        action = "检查是否有重复控制器或未结束的数据库事务"
    else:
        code, reason = "recovery_storage_unavailable", "恢复状态数据库暂时无法可靠读写"
        action = "检查恢复目录权限、磁盘与本机服务日志"
    return dict(error=code, remediation=f"{reason}。请 owner 保留恢复目录和已签消息，{action}。"
                "本轮未能完整对账，不能据此判断任务是否已执行；修复后会核对原消息，不会另签重复 continue。")


def private_directory(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("recovery path must be canonical")
    for item in (path, *path.parents):
        info = item.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or mode & 0o022 and not mode & stat.S_ISVTX):
            raise ValueError("unsafe recovery ancestor")
    info = path.lstat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("recovery directory must be owner-only 0700")
    return path


def read_private(path, *, limit=2 * 1024 * 1024):
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("recovery file must be canonical")
    # Config/env parents may be 0755; only writes by other users are forbidden.
    for parent in path.parents:
        info = parent.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or mode & 0o022 and not mode & stat.S_ISVTX):
            raise ValueError("unsafe recovery file ancestor")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or before.st_size > limit):
            raise ValueError("recovery file must be bounded, private and regular")
        content = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(content) > limit or before.st_size != len(content)
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError("recovery file changed during read")
    return content.decode("utf-8")



def _read_snapshot_private(path, *, limit=2 * 1024 * 1024):
    """Reopen only a proven atomic replacement of a UUID journal; never read unlink data."""
    path = Path(path)
    if path.suffix != ".json" or str(uuid.UUID(path.stem)) != path.stem:
        raise ValueError("recovery snapshot name mismatch")

    def private(info, *, linked=False):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > limit
                or linked and info.st_nlink != 1):
            raise ValueError("recovery file must be bounded, private and regular")

    def stable(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                info.st_size, info.st_mtime_ns)

    for _attempt in range(3):
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise ValueError("recovery file must be canonical")
        for parent in path.parents:
            info = parent.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
                    or mode & 0o022 and not mode & stat.S_ISVTX):
                raise ValueError("unsafe recovery file ancestor")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            private(before)

            def replaced():
                # The retained old inode must have been unlinked, without a
                # data/mode/owner change. A different named inode must pass
                # full admission. Discard old bytes and reopen; never waive a
                # generic read/JSON/permission failure or accept hardlinks.
                old = os.fstat(stream.fileno())
                private(old)
                if old.st_nlink != 0 or stable(old) != stable(before):
                    return False
                named = os.stat(path, follow_symlinks=False)
                private(named, linked=True)
                return (named.st_dev, named.st_ino) != (old.st_dev, old.st_ino)

            if before.st_nlink == 0 and replaced():
                continue
            private(before, linked=True)
            content = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            private(after)
            if len(content) > limit or before.st_size != len(content) or stable(before) != stable(after):
                raise ValueError("recovery file changed during read")
            if after.st_nlink == 0 and replaced():
                continue
            if after.st_nlink != 1 or before.st_ctime_ns != after.st_ctime_ns:
                raise ValueError("recovery file changed during read")
            named = os.stat(path, follow_symlinks=False)
            private(named, linked=True)
            if (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino):
                if replaced():
                    continue
                raise ValueError("recovery file changed during read")
            if stable(named) != stable(after) or named.st_ctime_ns != after.st_ctime_ns:
                raise ValueError("recovery file changed during read")
            return content.decode("utf-8")
    raise ValueError("recovery snapshot changed repeatedly during read")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate recovery JSON key")
        result[key] = value
    return result


def _json(text):
    return json.loads(text, object_pairs_hook=_unique)


def _absolute(value):
    if not isinstance(value, str) or not Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("absolute recovery path required")


def validate_config(config):
    fields = {"version", "owner_pubkey", "relay_url", "relay_pubkey", "owner_env_file", "state_dir", "agents"}
    if not isinstance(config, dict) or set(config) != fields or type(config["version"]) is not int or config["version"] != 1:
        raise ValueError("invalid recovery config schema")
    for key in ("owner_pubkey", "relay_pubkey"):
        if not isinstance(config[key], str) or not HEX64.fullmatch(config[key]):
            raise ValueError("recovery identity pin required")
    for key in ("owner_env_file", "state_dir"):
        _absolute(config[key])
    canonical_origin(config["relay_url"])
    agents = config["agents"]
    if not isinstance(agents, list) or not 1 <= len(agents) <= 64:
        raise ValueError("invalid recovery agent inventory")
    seen = {field: set() for field in ("name", "pubkey", "unit", "journal_dir")}
    for agent in agents:
        if not isinstance(agent, dict) or set(agent) != {"name", "pubkey", "unit", "env_file", "journal_dir", "revision", "binary_sha256"}:
            raise ValueError("invalid recovery agent schema")
        if not isinstance(agent["name"], str) or not NAME.fullmatch(agent["name"]):
            raise ValueError("invalid recovery agent name")
        for field in ("pubkey", "binary_sha256"):
            if not isinstance(agent[field], str) or not HEX64.fullmatch(agent[field]):
                raise ValueError("invalid recovery agent pin")
        if not isinstance(agent["revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", agent["revision"]):
            raise ValueError("full recovery revision required")
        if not isinstance(agent["unit"], str) or not re.fullmatch(r"buzz-[A-Za-z0-9_-]+\.service", agent["unit"]):
            raise ValueError("invalid recovery service unit")
        for field in ("env_file", "journal_dir"):
            _absolute(agent[field])
        for field, values in seen.items():
            if agent[field] in values:
                raise ValueError("duplicate recovery agent")
            values.add(agent[field])


def load_config(path):
    config = _json(read_private(path, limit=128 * 1024))
    validate_config(config)
    return config


def read_env(path):
    result = {}
    for raw in read_private(path, limit=1024 * 1024).splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("export "):
            raw = raw[7:].lstrip()
        key, sep, value = raw.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in result:
            raise ValueError("invalid recovery env syntax")
        if any(token in value for token in ("`", "$(", "${", "\x00")):
            raise ValueError("recovery env must be literal")
        tokens = shlex.split(value, comments=True)
        if len(tokens) > 1:
            raise ValueError("recovery env must contain one literal value")
        result[key] = tokens[0] if tokens else ""
    return result


def process_live(snapshot):
    """False only with proof of death/reboot/reuse; permission failure is unknown."""
    pid = snapshot.get("pid")
    if type(pid) is not int or pid <= 1 or not str(snapshot.get("process_start_ticks", "")).isdigit():
        raise ValueError("invalid runtime birth identity")
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if str(uuid.UUID(snapshot["boot_id"])) != snapshot["boot_id"]:
            raise ValueError("invalid boot identity")
        if boot_id != snapshot["boot_id"]:
            return False
        now = _identity(pid)
        return now[:2] == (snapshot["boot_id"], snapshot["process_start_ticks"]) and now[2] != "Z"
    except FileNotFoundError:
        return False


def load_snapshots(directory):
    directory = private_directory(directory)
    snapshots, total = [], 0
    for index, path in enumerate(directory.iterdir()):
        if index >= 2048:
            raise ValueError("recovery inventory directory is too large")
        if path.name == ".writer.lock":
            # Native owns the exclusive writer lease. The controller only
            # validates its private, empty regular file; it must not acquire it.
            read_private(path, limit=0)
            continue
        if path.name.startswith(".") and path.name.endswith(".tmp"):
            continue  # a killed atomic writer may leave an uncommitted temp file
        if path.suffix != ".json" or str(uuid.UUID(path.stem)) != path.stem:
            raise ValueError("unexpected recovery inventory entry")
        text = _read_snapshot_private(path)
        total += len(text.encode())
        if total > 16 * 1024 * 1024 or len(snapshots) >= 1024:
            raise ValueError("recovery inventory exceeds resource bound")
        snapshot = _json(text)
        if not isinstance(snapshot, dict) or snapshot.get("generation") != path.stem:
            raise ValueError("recovery snapshot name mismatch")
        snapshots.append(snapshot)
    return snapshots


def systemd_main_pid(unit):
    # No shell, owner key, Agent environment, or model-provided service name.
    try:
        if not isinstance(unit, str) or not re.fullmatch(r"buzz-[A-Za-z0-9_-]+\.service", unit):
            raise ValueError("invalid agent service unit")
        runtime = private_directory(Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.geteuid()}")))
        env = dict(XDG_RUNTIME_DIR=str(runtime), PATH="/usr/bin:/bin", LANG="C.UTF-8")
        result, output = run_bounded_text(
            ["/usr/bin/systemctl", "--user", "show", unit, "--property=MainPID", "--value"],
            timeout=10, env=env)
        value = output.strip()
        if (result.returncode or not re.fullmatch(r"0|[1-9][0-9]{0,9}", value)
                or int(value) > 2 ** 31 - 1):
            raise ValueError("invalid manager process identity")
        return int(value)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError("agent systemd state unavailable") from error


def _agent_env_matches(agent, config):
    env = read_env(agent["env_file"])
    return (wire._signer_pubkey(wire.secret_hex(env.get("BUZZ_PRIVATE_KEY", ""), "agent env")) == agent["pubkey"]
            and env.get("BUZZ_ACP_AGENT_OWNER") == config["owner_pubkey"]
            and canonical_origin(env.get("BUZZ_RELAY_URL", "")) == canonical_origin(config["relay_url"])
            and env.get("BUZZ_ACP_RECOVERY_DIR") == agent["journal_dir"]
            and env.get("BUZZ_ACP_RECOVERY_REVISION") == agent["revision"]
            and env.get("BUZZ_ACP_BINARY_SHA256") == agent["binary_sha256"])


def run_round(config, *, main_pid=systemd_main_pid, transport=None):
    validate_config(config)
    state = private_directory(config["state_dir"])
    env = read_env(config["owner_env_file"])
    if canonical_origin(env.get("BUZZ_RELAY_URL", "")) != canonical_origin(config["relay_url"]):
        raise ValueError("recovery owner community mismatch")
    key = wire.secret_hex(env.get("BUZZ_PRIVATE_KEY", ""), "owner env")
    if wire._signer_pubkey(key) != config["owner_pubkey"]:
        raise ValueError("recovery owner identity mismatch")
    transport = transport or RecoveryRelay(config["relay_url"], key, config["relay_pubkey"])
    if transport.owner != config["owner_pubkey"]:
        raise ValueError("recovery signer mismatch")
    # One lock covers the entire cross-Agent round, not just each outbox.
    with RecoveryStore(state / ".scheduler.sqlite3") as scheduling:
        schedule = RecoverySchedule(scheduling.db)
        tick = begin_tick(scheduling.db, config, os.environ.get("INVOCATION_ID"))
        result = _run_agents(config, main_pid, transport, state, schedule)
        complete_tick(scheduling.db, tick, result)
        return result


def _run_agents(config, main_pid, transport, state, schedule):
    result = {"version": 1, "agents": {}}
    for agent in schedule.agents(config["agents"]):
        entry = {"continued": 0, "errors": [], "scheduled": 0, "deferred": 0,
                 "retry_pending": 0, "next_retry_at": None, "retry_reasons": [], "continued_events": []}
        result["agents"][agent["name"]] = entry
        try:
            snapshots = load_snapshots(agent["journal_dir"])
            # Birth identities are inspected before declaring any generation dead.
            live = [s for s in snapshots if process_live(s)]
            dead = [s for s in snapshots if s not in live]
            pid = main_pid(agent["unit"])
            current = next((s for s in live if s["pid"] == pid), None)
            ready = False
            if len(live) > 1:
                entry["errors"].append("ambiguous_live_generations")
            elif current is not None:
                ready = _agent_env_matches(agent, config) and verify_process(
                    snapshot=current, main_pid=pid, binary_sha256=agent["binary_sha256"], revision=agent["revision"])
            if current is None:
                # Explicit unavailable sentinel: never satisfies readiness and
                # never invents a Thread. Only old verified bindings get notices.
                current = dict(version=JOURNAL_VERSION, generation=str(uuid.UUID(int=0)), agent_pubkey=agent["pubkey"],
                               relay=config["relay_url"], phase="unavailable", channels=[], active={}, cancelled_turns=[],
                               receipts={}, triggers={}, input_sources={}, recovery_receipts=[], recovery_attempts={}, deferred={}, shutdown_notices={})
                entry["errors"].append("runtime_journal_unavailable")
            with RecoveryStore(state / (agent["name"] + ".sqlite3")) as store:
                recovery = recover(store, transport, agent_name=agent["name"], agent_pubkey=agent["pubkey"],
                                   relay=config["relay_url"], current=current, snapshots=dead, runtime_verified=ready,
                                   schedule=schedule)
            entry["continued"] = recovery["continued"]
            entry["continued_events"] = recovery.get("continued_events", [])
            entry["scheduled"] = recovery.get("scheduled", 0)
            entry["deferred"] = recovery.get("deferred", 0)
            for field in ("retry_pending", "next_retry_at", "retry_reasons"):
                if field in recovery:
                    entry[field] = recovery[field]
            entry["errors"].extend(recovery["errors"])
            if ready and current.get("phase") == "starting" and "runtime_not_ready" in recovery["errors"]:
                # recover has verified a pending original route. Prewarm grants
                # no execution authority; only a later ready round may continue.
                entry["prewarming"] = request_prewarm(
                    snapshot=current, main_pid=lambda: main_pid(agent["unit"]),
                    binary_sha256=agent["binary_sha256"], revision=agent["revision"])
                if not entry["prewarming"]:
                    entry["errors"].append("runtime_prewarm_unverified")
        except sqlite3.Error as exc:
            # One Agent's broken outbox must not starve independent Agents.
            # Native journals and the frozen signed events remain untouched.
            diagnostic = storage_failure(exc)
            entry["errors"].append(diagnostic["error"])
            entry["remediation"] = diagnostic["remediation"]
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            # Do not leak env contents, tokens, model text or remote exceptions.
            entry["errors"].append("recovery_state_or_service_unverified")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="validate local config only; no relay writes")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        result = {"version": 1, "config_valid": True} if args.check else run_round(config)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return int(any(row["errors"] or row["retry_pending"] for row in result.get("agents", {}).values()))
    except sqlite3.Error as exc:
        print(json.dumps(storage_failure(exc), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        print('{"error":"recovery_configuration_unverified","remediation":"Check private config, identity pins, runtime journal and user service; no permission changes were made."}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
