"""Narrow user-manager adapter for recovery installation, not Agent restart.

Only the two canonical recovery units may be mutated. A completed controller
invocation is a separate proof from native/Agent readiness and installed timer
acceptance. This internal adapter does not report the feature installed.
"""
import os
from pathlib import Path
import re
import subprocess
import time

from audit_local_alignment import Auditor, run_bounded_text, trusted_directory_chain
from recovery_controller import load_config
from recovery_install_files import _validate_manager
from recovery_tick import verify_tick


SERVICE = "buzz-agent-recovery.service"
TIMER = "buzz-agent-recovery.timer"
COMMON = {"Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "FragmentPath",
          "DropInPaths", "NeedDaemonReload"}
PROPERTIES = {
    SERVICE: COMMON | {"MainPID", "ControlPID", "InvocationID", "Result", "ExecMainCode", "ExecMainStatus",
                       "ExecMainStartTimestampMonotonic", "ExecMainExitTimestampMonotonic"},
    TIMER: COMMON | {"Triggers", "LastTriggerUSecMonotonic"},
}
REMEDIATION = {
    "recovery_manager_unverified": "恢复服务的配置或用户服务管理器状态无法确认；请核对服务路径、附加配置和当前登录用户，未继续安装。",
    "recovery_manager_command_failed": "恢复服务的启停命令失败；请检查本机用户服务日志和权限，不能将本次安装视为成功。",
    "recovery_manager_not_quiescent": "旧恢复服务尚未完全停止；请等待进程退出并核对状态后重试，暂不能替换安装文件。",
    "recovery_tick_unverified": "未确认新版本恢复服务成功完成本次运行；请检查服务日志、Agent 运行版本和恢复队列，保留新配置向前修复。",
    "recovery_timer_unverified": "恢复定时器尚未同时启用并运行；请检查用户服务状态，自动恢复还不能视为可用。",
}


class ManagerError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

    def public(self):
        return dict(error=self.code, remediation=REMEDIATION[self.code], installed=False)


class RecoveryManager:
    def __init__(self, home, runtime_dir=None):
        self.home = Path(home)
        self.runtime = Path(runtime_dir if runtime_dir is not None
                            else os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.geteuid()}"))
        if (not self.home.is_absolute() or self.home.resolve(strict=True) != self.home
                or not trusted_directory_chain(self.home, os.geteuid())
                or not self.runtime.is_absolute() or not Auditor.trusted_private_directory(self.runtime)):
            raise ManagerError("recovery_manager_unverified")
        # Do not inherit an arbitrary bus address or any owner/Agent credential.
        self.env = dict(HOME=str(self.home), PATH="/usr/bin:/bin", LANG="C.UTF-8",
                        XDG_RUNTIME_DIR=str(self.runtime))

    def _call(self, *arguments, timeout=30):
        try:
            done, output = run_bounded_text(["/usr/bin/systemctl", "--user", *arguments],
                                            env=self.env, timeout=timeout)
            if done.returncode:
                raise ValueError("manager command failed")
            return output
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise ManagerError("recovery_manager_command_failed") from error

    def status(self, unit):
        if unit not in PROPERTIES:
            raise ManagerError("recovery_manager_unverified")
        output = self._call("show", unit, "--property=" + ",".join(sorted(PROPERTIES[unit])))
        try:
            fields = {}
            for line in output.splitlines():
                key, value = line.split("=", 1)
                if key in fields or len(value) > 4096:
                    raise ValueError("ambiguous manager properties")
                fields[key] = value
            if set(fields) != PROPERTIES[unit]:
                raise ValueError("unexpected manager properties")
            if (fields["Id"] != unit or fields["DropInPaths"] or fields["NeedDaemonReload"] != "no"
                    or fields["LoadState"] not in {"loaded", "not-found"}
                    or fields["FragmentPath"] != (str(self.home / ".config/systemd/user" / unit)
                                                  if fields["LoadState"] == "loaded" else "")):
                raise ValueError("unexpected effective unit")
            numeric = ({"MainPID", "ControlPID", "ExecMainCode", "ExecMainStatus",
                        "ExecMainStartTimestampMonotonic", "ExecMainExitTimestampMonotonic"}
                       if unit == SERVICE else {"LastTriggerUSecMonotonic"})
            if any(not re.fullmatch(r"0|[1-9][0-9]{0,19}", fields[key])
                   or int(fields[key]) >= 2 ** 64 for key in numeric):
                raise ValueError("invalid manager numeric property")
            if (fields["ActiveState"] not in {"active", "inactive", "activating", "deactivating", "failed", "reloading"}
                    or not re.fullmatch(r"[a-z-]{1,64}", fields["SubState"])
                    or fields["UnitFileState"] not in ({"static", "disabled", "enabled"} if unit == SERVICE else {"disabled", "enabled"})
                    and fields["LoadState"] != "not-found"):
                raise ValueError("unknown manager state")
            if fields["LoadState"] == "not-found" and (
                    fields["ActiveState"] != "inactive" or fields["SubState"] != "dead" or fields["UnitFileState"]
                    or unit == SERVICE and (fields["MainPID"] != "0" or fields["ControlPID"] != "0")):
                raise ValueError("missing unit still has execution state")
            if unit == TIMER and fields["LoadState"] == "loaded" and fields["Triggers"] != SERVICE:
                raise ValueError("timer targets another service")
            return fields
        except (ValueError, KeyError, TypeError) as error:
            raise ManagerError("recovery_manager_unverified") from error

    def capture(self):
        service, timer = self.status(SERVICE), self.status(TIMER)
        if timer["ActiveState"] not in {"active", "inactive"}:
            raise ManagerError("recovery_manager_unverified")
        return dict(service_present=service["LoadState"] == "loaded", timer_present=timer["LoadState"] == "loaded",
                    timer_was_enabled=timer["UnitFileState"] == "enabled", timer_was_active=timer["ActiveState"] == "active")

    def restore(self, previous):
        """Restore only the old timer state, after old files are durably restored.

        A publishing new tick is fenced by the caller: never call this after it.
        A running old process cannot be recreated; its durable work remains for
        the restored timer, rather than blindly restarting a completed oneshot.
        """
        try:
            _validate_manager(dict(before=previous, steps=[]))
        except ValueError as error:
            raise ManagerError("recovery_manager_unverified") from error
        self.reload()
        actual, service = self.capture(), self.status(SERVICE)
        if (any(actual[key] != previous[key] for key in ("service_present", "timer_present"))
                or service["ActiveState"] != "inactive" or service["MainPID"] != "0" or service["ControlPID"] != "0"
                or actual["timer_was_active"] or actual["timer_was_enabled"]):
            raise ManagerError("recovery_manager_unverified")
        if previous["timer_was_enabled"]:
            self._call("enable", TIMER)
        if previous["timer_was_active"]:
            self._call("start", TIMER)
        if self.capture() != previous:
            raise ManagerError("recovery_timer_unverified")

    def quiesce(self, *, expected=None):
        previous = self.capture()
        if expected is not None and previous != expected:
            raise ManagerError("recovery_manager_unverified")
        service, timer = self.status(SERVICE), self.status(TIMER)
        if timer["LoadState"] == "loaded":
            self._call("disable", "--now", TIMER)
        if service["LoadState"] == "loaded":
            self._call("stop", SERVICE, timeout=330)
            if self.status(SERVICE)["ActiveState"] == "failed":
                # A failed oneshot stays "failed" across stop. Clearing only
                # the marker executes nothing; the journal keeps the failure,
                # and the next verified start must prove a fresh success.
                self._call("reset-failed", SERVICE)
        service, timer = self.status(SERVICE), self.status(TIMER)
        if (service["ActiveState"] != "inactive" or service["SubState"] != "dead"
                or service["MainPID"] != "0" or service["ControlPID"] != "0"
                or timer["ActiveState"] != "inactive" or timer["UnitFileState"] not in {"disabled", ""}):
            raise ManagerError("recovery_manager_not_quiescent")
        return previous

    def reload(self):
        self._call("daemon-reload")

    def start_verified(self):
        before = self.status(SERVICE)
        if (before["LoadState"] != "loaded" or before["ActiveState"] != "inactive"
                or before["MainPID"] != "0" or before["ControlPID"] != "0"):
            raise ManagerError("recovery_tick_unverified")
        try:
            config = load_config(self.home / ".config/buzz/recovery/config.json")
        except (OSError, ValueError) as error:
            raise ManagerError("recovery_tick_unverified") from error
        started_after = time.monotonic_ns()
        self._call("start", SERVICE, timeout=330)
        after = self.status(SERVICE)
        if (after["ActiveState"] != "inactive" or after["SubState"] != "dead"
                or after["MainPID"] != "0" or after["ControlPID"] != "0"
                or after["Result"] != "success" or after["ExecMainStatus"] != "0"):
            raise ManagerError("recovery_tick_unverified")
        try:
            tick = verify_tick(config, started_after=started_after)
        except (OSError, ValueError) as error:
            raise ManagerError("recovery_tick_unverified") from error
        if after["InvocationID"]:
            if (after["InvocationID"] != tick["invocation_id"] or after["InvocationID"] == before["InvocationID"]
                    or after["ExecMainCode"] != "1"
                    or not started_after // 1000 <= int(after["ExecMainStartTimestampMonotonic"])
                    <= tick["started_ns"] // 1000 <= tick["finished_ns"] // 1000
                    <= int(after["ExecMainExitTimestampMonotonic"]) <= time.monotonic_ns() // 1000):
                raise ManagerError("recovery_tick_unverified")
        elif any(after[key] != "0" for key in ("ExecMainCode", "ExecMainStartTimestampMonotonic",
                                               "ExecMainExitTimestampMonotonic")):
            # A fully collected oneshot is expected; a partial or inconsistent
            # completion tuple is not proof. The durable receipt is mandatory
            # in both cases, and an old/manual/failed tick never passes.
            raise ManagerError("recovery_tick_unverified")
        return dict(invocation_id=tick["invocation_id"], installed=False)

    def enable_verified(self):
        self._call("enable", "--now", TIMER)
        value = self.status(TIMER)
        if value["LoadState"] != "loaded" or value["UnitFileState"] != "enabled" or value["ActiveState"] != "active":
            raise ManagerError("recovery_timer_unverified")
        return value
