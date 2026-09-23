"""The skill runs from two layouts: the repo checkout and the copy install.sh puts in a stable path
(the plugin cache path changes on every plugin update, so a timer cannot point into it)."""
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL = os.path.join(HERE, "..", "scripts", "install.sh")


def env_for(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return {"HOME": str(home), "PATH": os.environ["PATH"], "HARNESS_FAILOVER_DEST": str(tmp_path / "dest"),
            "HARNESS_FAILOVER_NO_SYSTEMD": "1"}, home


def test_installed_copy_runs_from_the_stable_location(tmp_path):
    env, home = env_for(tmp_path)
    r = subprocess.run(["bash", INSTALL], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["python3", str(tmp_path / "dest" / "harness-failover"), "profiles"], env=env,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "grok" in r.stdout and "claude-buzz" in r.stdout and "gpt-5.6-sol" in r.stdout


def test_install_without_systemd_touches_no_units(tmp_path):
    env, home = env_for(tmp_path)
    subprocess.run(["bash", INSTALL], env=env, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    assert not (home / ".config" / "systemd").exists()


def test_reinstall_replaces_the_previous_copy(tmp_path):
    env, _ = env_for(tmp_path)
    for _ in range(2):
        subprocess.run(["bash", INSTALL], env=env, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    stale = tmp_path / "dest" / "harness_failover" / "stale.py"
    stale.write_text("x")
    subprocess.run(["bash", INSTALL], env=env, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    assert not stale.exists()


def test_print_units_shows_the_timer_and_the_full_command(tmp_path):
    env, _ = env_for(tmp_path)
    r = subprocess.run(["bash", INSTALL, "--print-units"], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0
    assert "switch --to auto --apply --probe --retry" in r.stdout
    assert "OnCalendar=*:0/5" in r.stdout
    assert str(tmp_path / "dest" / "harness-failover") in r.stdout


def test_install_writes_both_units_and_enables_the_timer(tmp_path):
    """Uses a fake `systemctl` on PATH so no real systemd is touched."""
    env, home = env_for(tmp_path)
    env.pop("HARNESS_FAILOVER_NO_SYSTEMD")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "systemctl.log"
    fake = bindir / "systemctl"
    fake.write_text(f'#!/usr/bin/env bash\necho "$@" >> {log}\n')
    fake.chmod(0o755)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    r = subprocess.run(["bash", INSTALL], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    unitdir = home / ".config" / "systemd" / "user"
    service = (unitdir / "harness-failover.service").read_text()
    timer = (unitdir / "harness-failover.timer").read_text()
    assert service.startswith("[Unit]") and "ExecStart=/usr/bin/python3" in service and "--retry" in service
    assert timer.startswith("[Unit]") and "OnCalendar=*:0/5" in timer and "[Install]" in timer
    assert "harness-failover.timer" not in service  # the two files were split correctly
    calls = log.read_text().splitlines()
    assert "--user daemon-reload" in calls and "--user enable --now harness-failover.timer" in calls


import pytest  # noqa: E402


@pytest.mark.parametrize("bad", ["/", "HOME", "/tmp/has space/x", "/tmp/pct%x", "relative/path"])
def test_install_refuses_dangerous_or_unquotable_destinations(tmp_path, bad):
    env, home = env_for(tmp_path)
    env["HARNESS_FAILOVER_DEST"] = str(home) if bad == "HOME" else bad
    r = subprocess.run(["bash", INSTALL], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       cwd=tmp_path)  # a regressed guard must not write into the repo checkout
    assert r.returncode != 0 and "refus" in r.stderr.lower()


def test_installed_code_is_not_group_or_world_writable_even_with_a_loose_umask(tmp_path):
    env, _ = env_for(tmp_path)
    r = subprocess.run(["bash", "-c", f"umask 002; bash '{INSTALL}'"], env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    loose = []
    for root, dirs, files in os.walk(tmp_path / "dest"):
        for n in dirs + files:
            if os.stat(os.path.join(root, n)).st_mode & 0o022:
                loose.append(os.path.join(root, n))
    assert loose == []


def test_running_the_installed_tool_never_leaves_group_writable_bytecode(tmp_path):
    """Found on the real machine: the timer's first run created a group-writable __pycache__ next to code it
    executes every 5 minutes. The entrypoint must not write bytecode at all."""
    env, home = env_for(tmp_path)
    subprocess.run(["bash", INSTALL], env=env, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    dest = tmp_path / "dest"
    r = subprocess.run(["bash", "-c", f"umask 002; python3 '{dest}/harness-failover' profiles"], env=env,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    caches = [p for p in dest.rglob("*") if p.name == "__pycache__" or p.suffix == ".pyc"]
    assert caches == []
    loose = [str(p) for p in [dest, *dest.rglob("*")] if os.stat(p).st_mode & 0o022]
    assert loose == []


def test_the_service_unit_pins_a_safe_umask_and_disables_bytecode():
    r = subprocess.run(["bash", INSTALL, "--print-units"], env={"HOME": "/home/u", "PATH": os.environ["PATH"]},
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert "UMask=0022" in r.stdout and "PYTHONDONTWRITEBYTECODE=1" in r.stdout
