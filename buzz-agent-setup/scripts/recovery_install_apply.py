"""Installation transaction and explicit repair behind the public installer CLI.

File publication is not runtime proof. The external user-manager and actual
native process must independently prove the default-enabled installation.
"""
import base64
import json
from pathlib import Path
import re

from install_agent_recovery import PlanError, prepare_install
from audit_local_alignment import audit_home
from compare_local_alignment_receipts import compare
from recovery_controller import _json, validate_config
from recovery_install_files import (FORWARD, InstallationFileError, InstallationFiles, _directory,
                                    installation_status)
from recovery_install_manager import SERVICE, ManagerError, RecoveryManager
from recovery_install_runtime import RuntimeProofError, ensure_ready, inspect_inventory
from recovery_tick import config_digest, read_tick


ERRORS = (PlanError, InstallationFileError, ManagerError, RuntimeProofError)


class ApplyError(ValueError):
    def __init__(self, stage, error, phase=None):
        super().__init__("recovery_installation_failed")
        self.stage, self.error, self.phase = stage, error, phase

    @property
    def outcome(self):
        """Derived from the durable journal phase, never from the error text."""
        if isinstance(self.error, InstallationFileError) and self.error.code == "installation_interrupted":
            return "needs_repair"
        if self.phase is None:
            return "refused"
        return "rolled_back" if self.phase == "rolled_back" else "needs_repair"

    def public(self):
        result = self.error.public() if isinstance(self.error, ERRORS) else dict(
            error="recovery_installation_failed", remediation="恢复安装未完成；请核对安装记录和用户服务日志，保留现有任务后修复。")
        cause = self.error.__cause__
        while cause is not None:
            if isinstance(cause, ERRORS) and not isinstance(cause, InstallationFileError):
                result["cause"] = cause.public()
                if (isinstance(self.error, InstallationFileError)
                        and self.error.code == "installation_write_failed"):
                    # This wrapper is raised only after the file/manager
                    # rollback completed. Show the actual preflight/manager
                    # failure, not a misleading disk-write diagnosis. Never
                    # replace rollback conflicts or the publication fence.
                    result.update(cause.public(), rollback="completed")
                break
            cause = cause.__cause__
        return {**result, "stage": self.stage, "outcome": self.outcome, "installed": False}


CONFIG_ARGUMENT = "%h/.config/buzz/recovery/config.json"


def render_units(release, *, config=CONFIG_ARGUMENT, service=SERVICE):
    """The one controller service/timer template.

    Production always uses the canonical config argument and unit name. The L3
    harness reuses this exact text with a test-only unit name and config path,
    so the timer it proves is the one the installer writes.
    """
    # Do not interpolate arbitrary systemd specifiers, shell escapes or lines.
    if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(release))
            or not re.fullmatch(r"(%h|/[A-Za-z0-9_.-]+)(/[A-Za-z0-9_.-]+)+", config)
            or ".." in str(release).split("/") + config.split("/")
            or not re.fullmatch(r"buzz-[a-z0-9-]+\.service", service)):
        raise PlanError("recovery_unit_path_unsupported", "host",
                        "当前安装目录含服务模板不支持的字符；请使用受支持的规范路径，未执行服务启停。")
    unit = ("[Unit]\nAfter=network-online.target\n[Service]\nType=oneshot\nUMask=0077\n"
            "NoNewPrivileges=yes\nTimeoutStartSec=5min\nEnvironment=PATH=/usr/bin:/bin\n"
            f"ExecStart=/usr/bin/python3 -I {release}/scripts/recovery_controller.py "
            f"--config {config}\n")
    timer = ("[Timer]\nOnActiveSec=1min\nOnBootSec=2min\nOnUnitActiveSec=15s\nPersistent=true\n"
             f"Unit={service}\n[Install]\nWantedBy=timers.target\n")
    return dict(service=unit.encode(), timer=timer.encode())


def render_files(home, revision, config):
    home = Path(home)
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(home)):
        raise PlanError("recovery_unit_path_unsupported", "host",
                        "当前安装目录含服务模板不支持的字符；请使用受支持的规范路径，未执行服务启停。")
    release = home / ".local/share/buzz-agent-setup/releases" / revision
    return dict(config=(json.dumps(config, sort_keys=True) + "\n").encode(), **render_units(release))


def check_install(*, home, revision, owner_env_file, relay_pubkey, manager=None, main_pid=None):
    """Read-only --check: journal, plan/audit, manager state and live runtime.

    No file, directory, lock, unit or signal is written or sent; a PASS says
    apply may start, never that recovery is installed.
    """
    stage = "journal"
    try:
        journal = installation_status(home)
        if journal != "clean":
            raise InstallationFileError("installation_busy" if journal == "busy" else "installation_interrupted")
        stage = "plan"
        plan = prepare_install(home=home, revision=revision, owner_env_file=owner_env_file, relay_pubkey=relay_pubkey)
        render_files(home, revision, plan["config"])
        stage = "manager"
        manager = manager if manager is not None else RecoveryManager(home)
        state = manager.capture()
        stage = "runtime_before"
        runtime = inspect_inventory(plan["config"], **({} if main_pid is None else dict(main_pid=main_pid)))
        return dict(installed=False, journal=journal, plan_valid=True, default_enabled=True,
                    timer_seconds=plan["timer_seconds"], agents=[a["name"] for a in plan["config"]["agents"]],
                    manager=state, runtime=runtime)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ApplyError(stage, error) from error


def _phase(transaction):
    return None if transaction is None or transaction.record is None else transaction.record["phase"]


def apply_install(*, home, revision, owner_env_file, relay_pubkey, manager=None, main_pid=None):
    stage, transaction = "plan", None
    try:
        plan = prepare_install(home=home, revision=revision, owner_env_file=owner_env_file, relay_pubkey=relay_pubkey)
        files = render_files(home, revision, plan["config"])
        manager = manager if manager is not None else RecoveryManager(home)
        transaction = InstallationFiles(home, files, rollback_manager=manager.restore)
        with transaction:
            # The initial preflight is outside the lock. Re-read it under the
            # lock before any service mutation; file publication also CAS-checks
            # all three targets, and post-audit catches unrelated owner drift.
            current = prepare_install(home=home, revision=revision, owner_env_file=owner_env_file, relay_pubkey=relay_pubkey)
            if current != plan:
                raise PlanError("installation_plan_changed", "local-inventory",
                                "安装前清单或配置发生变化；请核对并重新生成计划，未继续服务启停。")
            # Forward repair after a crash needs the exact pre-install topology.
            transaction.baseline(json.dumps(plan["before_audit"], sort_keys=True).encode())
            stage = "runtime_before"
            proof_args = {} if main_pid is None else dict(main_pid=main_pid)
            ensure_ready(plan["config"], **proof_args)
            stage = "quiesce"
            previous = manager.capture()
            transaction.manager_before(previous)
            manager.quiesce(expected=previous)
            transaction.manager_step("quiesced")
            stage = "publish"
            transaction.publish()
            stage = "reload"
            manager.reload()
            transaction.manager_step("reloaded")
            stage = "state"
            # Bootstrap only the canonical directory from the validated plan.
            # Never chmod or reset existing state; failure is still before the
            # first tick and therefore permits the file/manager rollback.
            _directory(Path(plan["config"]["state_dir"]), private=True)
            stage = "tick"
            transaction.runtime_started()
            tick = manager.start_verified()
            transaction.manager_step("tick_completed")
            stage = "runtime_after"
            runtime = ensure_ready(plan["config"], **proof_args)
            stage = "enable_timer"
            manager.enable_verified()
            transaction.manager_step("timer_enabled")
            stage = "post_audit"
            after = audit_home(Path(home), revision)
            compare(plan["before_audit"], after, revision, allow_recovery_install=True)
            transaction.manager_step("post_audit_passed")
            stage = "commit"
            transaction.commit()
        return dict(installed=True, operation=transaction.record["operation"], tick=tick,
                    runtime=runtime, after_audit=after)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ApplyError(stage, error, _phase(transaction)) from error


def _idle(service):
    return service["MainPID"] == "0" and service["ControlPID"] == "0" and service["ActiveState"] in {"inactive", "failed"}


def _repair_restore(manager):
    """Old manager state after the exact file restore, from any partial step.

    A crash may stop before, inside or after quiesce, or inside an earlier
    restore. Reload the restored files; if the manager already matches the
    durable baseline nothing is touched, otherwise quiesce and restore it.
    """
    def restore(previous):
        manager.reload()
        if manager.capture() == previous and _idle(manager.status(SERVICE)):
            return
        manager.quiesce()
        manager.restore(previous)
    return restore


def _image(item):
    return base64.b64decode(item["data"], validate=True)


def _no_new_runtime_evidence(record):
    """A pre-fence journal must not coexist with a tick of the NEW config."""
    config = record["files"]["config"]
    if config["before"] == config["after"]:
        return  # identical config: the rollback does not change what ran
    value = _json(_image(config["after"]))
    validate_config(value)
    try:
        (Path(value["state_dir"]) / ".scheduler.sqlite3").lstat()
    except FileNotFoundError:
        return
    try:
        tick = read_tick(value["state_dir"])
    except ValueError as error:
        raise InstallationFileError("installation_repair_runtime_evidence") from error
    if tick["config_sha256"] == config_digest(value):
        raise InstallationFileError("installation_repair_runtime_evidence")


def _forward_preconditions(transaction, *, home, revision, owner_env_file, relay_pubkey):
    try:
        baseline = transaction.read_baseline()
    except (OSError, ValueError) as error:
        raise InstallationFileError("installation_repair_baseline_missing") from error
    plan = prepare_install(home=home, revision=revision, owner_env_file=owner_env_file, relay_pubkey=relay_pubkey)
    files = render_files(home, revision, plan["config"])
    if any(files[name] != _image(transaction.record["files"][name]["after"]) for name in files):
        raise PlanError("installation_plan_changed", "local-inventory",
                        "当前发布计划与已发布的恢复文件不一致；只能向前完成原安装，请使用原发布版本和凭据重新执行 --repair。")
    return plan, baseline


def _step(transaction, name):
    if name not in transaction.record["manager"]["steps"]:
        transaction.manager_step(name)


def repair_install(*, home, revision, owner_env_file, relay_pubkey, manager=None, main_pid=None):
    """Explicitly finish one interrupted installation: exact rollback or forward only.

    Before the runtime fence the verified backups are restored; after it (or
    with any doubt that the new controller ran) files are never rolled back
    and success needs the same full post-install proof as apply. Ambiguous
    state is refused before any journal, file or manager change.
    """
    stage, transaction, adopted = "repair", None, None
    try:
        manager = manager if manager is not None else RecoveryManager(home)
        adopted = InstallationFiles.adopt(home, rollback_manager=_repair_restore(manager))
        if adopted is None:
            return dict(repair="not_needed", installed=False)
        try:
            forward = adopted.record["phase"] in FORWARD
            known, published = adopted.file_matches()
            if not (published if forward else known):
                raise InstallationFileError("installation_rollback_conflict")
            if not _idle(manager.status(SERVICE)):
                raise InstallationFileError("installation_repair_runtime_active")
            if forward:
                plan, baseline = _forward_preconditions(adopted, home=home, revision=revision,
                                                        owner_env_file=owner_env_file, relay_pubkey=relay_pubkey)
            else:
                _no_new_runtime_evidence(adopted.record)
        except BaseException:
            adopted.release()
            raise
        transaction = adopted
        proof_args = {} if main_pid is None else dict(main_pid=main_pid)
        with transaction:
            if forward:
                stage = "forward"
                transaction.resume_forward()
                manager.quiesce()
                stage = "runtime_before"
                ensure_ready(plan["config"], **proof_args)
                stage = "tick"
                tick = manager.start_verified()
                _step(transaction, "tick_completed")
                stage = "runtime_after"
                runtime = ensure_ready(plan["config"], **proof_args)
                stage = "enable_timer"
                manager.enable_verified()
                _step(transaction, "timer_enabled")
                stage = "post_audit"
                after = audit_home(Path(home), revision)
                compare(baseline, after, revision, allow_recovery_install=True)
                _step(transaction, "post_audit_passed")
                stage = "commit"
                transaction.commit()
            # Otherwise leaving the adopted pre-fence transaction uncommitted
            # performs the same verified exact rollback as a failed apply.
        operation = transaction.record["operation"]
        if not forward:
            return dict(repair="rolled_back", installed=False, operation=operation)
        return dict(repair="completed", installed=True, operation=operation, tick=tick,
                    runtime=runtime, after_audit=after)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ApplyError(stage, error, _phase(transaction)) from error

