"""Real, uniquely owned oneshot probe for the recovery L3 runner.

This exercises the production controller and durable reader, not the canonical
installer or automatic timer. No production units are changed. The independent
oracle recomputes binding/freshness from the input and actual process clock.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from recovery_tick import read_tick, verify_tick


PROPERTIES = ("Id", "LoadState", "ActiveState", "SubState", "MainPID", "ControlPID", "InvocationID",
              "ExecMainCode", "ExecMainStatus", "ExecMainStartTimestampMonotonic", "ExecMainExitTimestampMonotonic")


def oracle(config, receipt, result, started_after, finished_before, previous_id):
    expected_ok = not any(row["errors"] or row["retry_pending"] for row in result["agents"].values())
    assert receipt["version"] == 1 and receipt["origin"] == "systemd"
    assert receipt["phase"] == "completed" and receipt["ok"] is expected_ok
    assert receipt["invocation_id"] != previous_id
    assert re.fullmatch(r"[0-9a-f]{32}", receipt["invocation_id"]) and receipt["invocation_id"] != "0" * 32
    assert receipt["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    expected_digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert receipt["config_sha256"] == expected_digest
    assert started_after <= receipt["started_ns"] <= receipt["finished_ns"] <= finished_before
    return expected_ok


def run_tick(controller, config_path, *, observations):
    config = json.loads(config_path.read_text())
    database = Path(config["state_dir"]) / ".scheduler.sqlite3"
    previous = read_tick(config["state_dir"])["invocation_id"] if database.exists() else None
    unit = "buzz-recovery-probe-" + uuid.uuid4().hex + ".service"
    env = dict(PATH="/usr/bin:/bin", LANG="C.UTF-8", XDG_RUNTIME_DIR=os.environ["XDG_RUNTIME_DIR"])
    record = dict(unit=unit, status="failed", previous_invocation=previous)
    observations.append(record)

    def manager(*arguments, check=True):
        return subprocess.run(["/usr/bin/systemctl", "--user", *arguments], env=env,
                              capture_output=True, text=True, timeout=30, check=check)

    def show():
        result = manager("show", unit, "--property=" + ",".join(PROPERTIES))
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines())
        assert set(fields) == set(PROPERTIES) and fields["Id"] == unit
        return fields

    before = show()
    assert before["LoadState"] == "not-found" and before["MainPID"] == "0" and before["ControlPID"] == "0"
    try:
        start = time.monotonic_ns()
        done = subprocess.run([
            "/usr/bin/systemd-run", "--user", "--wait", "--pipe", "--unit=" + unit,
            "--property=Type=oneshot", "--property=UMask=0077", "--property=NoNewPrivileges=yes",
            "--property=TimeoutStartSec=2min", "--property=Environment=PATH=/usr/bin:/bin",
            "/usr/bin/python3", "-I", str(controller), "--config", str(config_path)],
            env=env, capture_output=True, text=True, timeout=150)
        finish = time.monotonic_ns()
        record.update(started_after=start, finished_before=finish, command_returncode=done.returncode,
                      manager_diagnostic=done.stderr[-2000:])
        result = json.loads(done.stdout)
        receipt = read_tick(config["state_dir"])
        after = show()
        record.update(receipt=receipt, after=after)
        ok = oracle(config, receipt, result, start, finish, previous)
        assert done.returncode == int(not ok)
        assert after["MainPID"] == "0" and after["ControlPID"] == "0"
        assert after["ActiveState"] in {"inactive", "failed"}
        if after["InvocationID"]:
            assert after["InvocationID"] == receipt["invocation_id"]
        if ok:
            assert verify_tick(config, started_after=start) == receipt
        else:
            try:
                verify_tick(config, started_after=start)
            except ValueError:
                record["failed_tick_rejected"] = True
            else:
                raise AssertionError("failed real controller tick counted as successful installation")
        record["status"] = "passed"
        return done
    finally:
        manager("stop", unit, check=False)
        manager("reset-failed", unit, check=False)
        after_cleanup = show()
        record["after_cleanup"] = after_cleanup
        assert after_cleanup["ActiveState"] == "inactive" and after_cleanup["MainPID"] == "0"
        assert after_cleanup["ControlPID"] == "0"
