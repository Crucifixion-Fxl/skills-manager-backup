#!/usr/bin/python3 -I
"""Owner-only recovery installer: --check, --dry-run, --apply or --repair.

Plans from the full alignment inventory, not the invitation service's role
subset. --apply runs the journaled file/manager transaction and reports
installed only after its full post-install proof; --repair finishes an
interrupted transaction (exact rollback before the runtime fence, forward
only after it). --check and --dry-run never write.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_local_alignment import audit_home, trusted_directory_chain
from recovery_controller import JOURNAL_VERSION, _json, load_config, read_env, validate_config
from recovery_relay import canonical_origin, wire


class PlanError(ValueError):
    def __init__(self, code, subject, remediation, *, checks=()):
        super().__init__(code)
        self.code, self.subject, self.remediation = code, subject, remediation
        self.checks = [{key: item[key] for key in ("category", "subject", "status", "code")}
                       for item in checks]

    def public(self):
        return dict(error=self.code, subject=self.subject, remediation=self.remediation,
                    checks=self.checks, installed=False)


CAPABILITIES = {
    "version": JOURNAL_VERSION, "durable_admission": True,
    "cancellation_tombstones": True, "startup_prewarm": True,
    "prewarm_signal": "SIGUSR1", "runtime_policy_snapshot": True,
    "original_source_bindings": True, "deferred_same_attempt": True,
    "durable_denial_notices": True, "durable_shutdown_notices": True,
    "exact_attempt_binding": True, "generation_fence": True, "delivery_receipts": True,
    "durable_storage_feedback": True, "bounded_terminal_retention": True,
}


def _capability_output_limit():
    resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))


def probe_binary(path, digest):
    """Execute the verified open ELF, with no owner/Agent credential environment."""
    descriptor, child = None, None
    try:
        path = Path(path)
        if (not path.is_absolute() or path.resolve(strict=True) != path
                or not trusted_directory_chain(path.parent, os.geteuid())):
            raise ValueError("untrusted capability path")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid not in (0, os.geteuid())
                or stat.S_IMODE(before.st_mode) & 0o022 or not before.st_mode & 0o111
                or not 4 <= before.st_size <= 1024 * 1024 * 1024):
            raise ValueError("untrusted capability binary")
        if os.read(descriptor, 4) != b"\x7fELF":
            raise ValueError("capability program must be ELF")
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != digest:
            raise ValueError("capability digest mismatch")
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen(
                [f"/proc/self/fd/{descriptor}", "recovery-schema"], pass_fds=(descriptor,),
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                start_new_session=True, preexec_fn=_capability_output_limit,
            )
            status = child.wait(timeout=10)
            output.seek(0)
            payload = output.read(4097)
            if len(payload) > 4096:
                raise ValueError("capability output exceeded limit")
            value = _json(payload)
        after = os.fstat(descriptor)
        if (status or not isinstance(value, dict) or value != CAPABILITIES
                or type(value.get("version")) is not int
                or any(value.get(key) is not True for key, expected in CAPABILITIES.items() if expected is True)
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError("capability contract mismatch")
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        raise PlanError("binary_recovery_capability_unverified", "native-binary",
                        "请安装并核对本次升级的固定原生二进制及摘要；恢复能力版本必须与控制器一致。未启动服务。") from error
    finally:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        if descriptor is not None:
            os.close(descriptor)


def _check_existing_bindings(path, desired):
    """A release upgrade may change code pins, never transfer existing work."""
    try:
        path.lstat()
    except FileNotFoundError:
        return
    try:
        current = load_config(path)
    except (OSError, ValueError) as error:
        raise PlanError("existing_recovery_config_unverified", "recovery-config",
                        "已有恢复配置无法安全读取；请先核对文件权限、格式和未完成任务，不能直接覆盖。") from error
    immutable = ("owner_pubkey", "relay_pubkey", "owner_env_file", "state_dir")
    if (any(current[key] != desired[key] for key in immutable)
            or canonical_origin(current["relay_url"]) != canonical_origin(desired["relay_url"])):
        raise PlanError("recovery_state_binding_changed", "recovery-config",
                        "已有恢复记录的 owner、Relay 或状态目录与本次配置不一致；请先核对归属，未转移或覆盖原任务。")
    agents = {agent["name"]: agent for agent in desired["agents"]}
    for old in current["agents"]:
        name = old["name"]
        if name not in agents:
            raise PlanError("removed_agent_requires_reconciliation", name,
                            "已有恢复记录中的 Agent 不在本次清单；请先核对其未完成任务和停用记录，不能静默漏装或删除。")
        if any(old[key] != agents[name][key] for key in ("pubkey", "unit", "env_file", "journal_dir")):
            raise PlanError("recovery_state_binding_changed", name,
                            "该 Agent 的身份或恢复记录路径发生变化；请先核对未完成任务归属，不能将旧任务交给另一身份。")


def prepare_install(*, home, revision, owner_env_file, relay_pubkey):
    """Pure local preflight; all deployment inputs remain byte-for-byte unchanged."""
    home = Path(home)
    if sys.platform != "linux":
        raise PlanError("platform_unsupported", "host", "恢复服务仅支持 Linux/systemd；请在受支持主机完成安装。")
    if (not home.is_absolute() or home.resolve(strict=True) != home
            or not trusted_directory_chain(home, os.geteuid())):
        raise PlanError("home_untrusted", "host", "请核对 owner 目录的真实路径和写权限，未修改文件。")
    if not isinstance(revision, str) or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise PlanError("release_revision_invalid", "release", "请使用已验证 main 的完整 40 位发布版本。")
    report = audit_home(home, revision)
    # Missing first-install service and verified configuration coverage/pin
    # changes are repairable. Never exempt executable/unit safety failures:
    # even stopping an old unit could execute its unverified ExecStop.
    service = home / ".config/systemd/user/buzz-agent-recovery.service"
    service_absent = not service.exists() and not service.is_symlink()
    repairable = {"agent_coverage_mismatch", "agent_runtime_binding_mismatch",
                  "recovery_timer_not_enabled_or_active"}
    if service_absent:
        repairable.add("required_service_not_deployed")
    blockers = [c for c in report["checks"] if c["status"] == "unknown"
                or c["status"] == "fail" and not (c["category"] == "recovery_release" and c["code"] in repairable)]
    if blockers:
        raise PlanError("alignment_required", "local-inventory",
                        "请先按本机升级清单修复列出的缺项；未漏掉失败 Agent，也未启用不完整配置。", checks=blockers)
    try:
        owner_env = read_env(owner_env_file)
        owner = wire._signer_pubkey(wire.secret_hex(owner_env.get("BUZZ_PRIVATE_KEY", ""), "owner env"))
        relay = canonical_origin(owner_env.get("BUZZ_RELAY_URL", ""))
    except (OSError, ValueError) as error:
        raise PlanError("owner_identity_unverified", "owner",
                        "请核对 owner 的私有凭据文件和固定 Relay；不会借用其它身份。") from error
    config = dict(version=1, owner_pubkey=owner, relay_url=relay, relay_pubkey=relay_pubkey,
                  owner_env_file=str(owner_env_file), state_dir=str(home / ".local/state/buzz-recovery/controller"), agents=[])
    checked_binaries = set()
    for name in report["inventory_ids"]["agents"]:
        path = home / ".config/buzz/agents" / f"{name}.env"
        try:
            env = read_env(path)
            pubkey = wire._signer_pubkey(wire.secret_hex(env.get("BUZZ_PRIVATE_KEY", ""), "agent env"))
            journal = str(home / ".local/state/buzz-recovery" / name / "runtime")
            if (env.get("BUZZ_ACP_AGENT_OWNER") != owner
                    or canonical_origin(env.get("BUZZ_RELAY_URL", "")) != relay
                    or env.get("BUZZ_ACP_RECOVERY_REVISION") != revision
                    or env.get("BUZZ_ACP_RECOVERY_DIR") != journal):
                raise ValueError("agent runtime identity binding mismatch")
            binary = (env["BUZZ_ACP_BINARY"], env["BUZZ_ACP_BINARY_SHA256"])
            if binary not in checked_binaries:
                probe_binary(*binary)
                checked_binaries.add(binary)
            config["agents"].append(dict(name=name, pubkey=pubkey, unit=f"buzz-local-{name}.service",
                                         env_file=str(path), revision=revision, binary_sha256=binary[1],
                                         journal_dir=journal))
        except (OSError, ValueError, KeyError) as error:
            if isinstance(error, PlanError):
                raise
            raise PlanError("agent_identity_unverified", name,
                            "请核对该 Agent 的独立身份、owner、Relay 和本次发布配置；不会修改响应权限。") from error
    validate_config(config)
    _check_existing_bindings(home / ".config/buzz/recovery/config.json", config)
    return dict(config=config, before_audit=report, default_enabled=True, timer_seconds=15, installed=False)


EXIT = {"ok": 0, "refused": 3, "rolled_back": 4, "needs_repair": 5}
NEXT = {
    "refused": "未修改任何文件或服务；按 remediation 处理后重新执行 --check。",
    "rolled_back": "安装失败，已按持久备份恢复原文件和定时器状态；修复原因后重新执行 --check 再 --apply。",
    "needs_repair": "安装未完成或新服务可能已处理任务；不要删除安装记录或直接重装，先 --check 再执行 --repair。",
}
EPILOG = ("exit codes: 0 ok; 2 usage; 3 refused (nothing changed); 4 failed and rolled back; "
          "5 needs --repair. Output is one JSON line; installed=true only after apply/repair proved "
          "a fresh tick receipt, timer enabled+active, the full live inventory and a post-audit PASS.")


def _installed(result):
    """Public summary; installed only with apply's full post-install proof."""
    out = dict(installed=result["installed"] is True, operation=result.get("operation"))
    if "repair" in result:
        out["repair"] = result["repair"]
    if out["installed"]:
        out.update(agents=[proof["name"] for proof in result["runtime"]], runtime=result["runtime"],
                   tick=dict(invocation_id=result["tick"]["invocation_id"]),
                   timer=dict(enabled=True, active=True), timer_seconds=15,
                   after_audit=dict(ok=result["after_audit"]["ok"], summary=result["after_audit"]["summary"]))
    return out


def main(argv=None, *, home=None, manager=None, main_pid=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=EPILOG)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_const", dest="mode", const="check",
                      help="read-only: journal, plan, audit, user-manager and live runtime preflight")
    mode.add_argument("--dry-run", action="store_const", dest="mode", const="dry-run",
                      help="read-only: print the planned recovery inventory only")
    mode.add_argument("--apply", action="store_const", dest="mode", const="apply",
                      help="install or upgrade in one journaled transaction with verified rollback")
    mode.add_argument("--repair", action="store_const", dest="mode", const="repair",
                      help="finish an interrupted install: exact rollback before the runtime fence, forward only after")
    parser.add_argument("--revision", required=True, help="the verified 40-hex release revision")
    parser.add_argument("--owner-env-file", required=True, type=Path, help="owner private env (never printed)")
    parser.add_argument("--relay-pubkey", required=True, help="the fixed relay public key")
    args = parser.parse_args(argv)
    import recovery_install_apply as install  # lazy: that module imports this one

    options = dict(home=home if home is not None else Path.home().resolve(), revision=args.revision,
                   owner_env_file=args.owner_env_file, relay_pubkey=args.relay_pubkey)
    boundaries = dict(manager=manager, main_pid=main_pid)
    try:
        if args.mode == "dry-run":
            plan = prepare_install(**options)
            result = dict(installed=False, plan_valid=True, default_enabled=True, timer_seconds=plan["timer_seconds"],
                          agents=[a["name"] for a in plan["config"]["agents"]])
        elif args.mode == "check":
            result = install.check_install(**options, **boundaries)
        elif args.mode == "apply":
            result = _installed(install.apply_install(**options, **boundaries))
        else:
            result = _installed(install.repair_install(**options, **boundaries))
        outcome = "ok"
    except PlanError as error:
        result, outcome = error.public(), "refused"
    except install.ApplyError as error:
        result = error.public()
        outcome = result.pop("outcome")
    except (OSError, ValueError, TypeError, KeyError):
        # Unclassified: a read-only mode changed nothing; a writing mode may have.
        outcome = "refused" if args.mode in {"check", "dry-run"} else "needs_repair"
        result = dict(error="installation_unverified", installed=False,
                      remediation="安装状态无法核对；请保留安装记录与服务日志，先执行 --check。")
    if outcome != "ok":
        result["next"] = NEXT[outcome]
    print(json.dumps(dict(result, mode=args.mode, outcome=outcome), ensure_ascii=False, sort_keys=True))
    return EXIT[outcome]

if __name__ == "__main__":
    raise SystemExit(main())
