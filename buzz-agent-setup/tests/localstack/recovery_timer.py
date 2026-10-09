"""Installed-timer driver for the recovery L3 runner. Test-only unit names.

The service and timer text comes from the installer's own template
(``recovery_install_apply.render_units``); only the unit name and the config
path differ. Units live in the user manager's runtime directory and are
enabled with ``--runtime``: never ``buzz-agent-recovery.*``, never ~/.config.
Recovery is observed purely from timer-triggered invocations: this driver has
no way to start the controller itself.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from recovery_install_apply import render_units
from recovery_tick import read_tick

NAME = re.compile(r"buzz-recovery-l3-[a-z0-9]{6,32}")
SERVICE_PROPERTIES = ("Id", "LoadState", "ActiveState", "MainPID", "ControlPID", "FragmentPath")
TIMER_PROPERTIES = ("Id", "LoadState", "ActiveState", "UnitFileState", "FragmentPath", "Triggers",
                    "LastTriggerUSec", "NextElapseUSecMonotonic")


class TimerError(RuntimeError):
    pass


class InstalledTimer:
    def __init__(self, name, *, release, config_path, state_dir, runtime_dir=None):
        if not NAME.fullmatch(name):
            raise ValueError("L3 timer must use a unique buzz-recovery-l3-<id> name")
        self.runtime = Path(runtime_dir or os.environ["XDG_RUNTIME_DIR"])
        self.unit_dir = self.runtime / "systemd/user"
        self.service, self.timer = name + ".service", name + ".timer"
        self.release, self.config_path, self.state_dir = Path(release), Path(config_path), state_dir
        self.env = dict(PATH="/usr/bin:/bin", LANG="C.UTF-8", XDG_RUNTIME_DIR=str(self.runtime))
        self.created = []        # exact files/directories this driver created
        self.commands = []       # every manager command, for the report
        self.receipts = {}       # invocation id -> durable tick receipt read after it finished
        self.consumed = []       # invocation ids handed to the runner, in order
        self.floor = None        # only invocations started at/after this realtime are consumable
        self.pauses = []
        self.installed_at = None
        self.found = {}          # latest journal view, refreshed by the observer thread
        self.missed = set()      # finished invocations whose receipt was overwritten before readback
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.observer = None
        self.observer_errors = []

    # ------------------------------------------------------------- manager
    def _systemctl(self, *arguments, check=True):
        self.commands.append(list(arguments))
        return subprocess.run(["/usr/bin/systemctl", "--user", *arguments], env=self.env,
                              capture_output=True, text=True, timeout=60, check=check)

    def show(self, unit):
        properties = SERVICE_PROPERTIES if unit == self.service else TIMER_PROPERTIES
        output = subprocess.run(["/usr/bin/systemctl", "--user", "show", unit, "--property=" + ",".join(properties)],
                                env=self.env, capture_output=True, text=True, timeout=30, check=True).stdout
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    def install(self):
        units = render_units(self.release, config=str(self.config_path), service=self.service)
        if not self.unit_dir.exists():
            self.unit_dir.mkdir(mode=0o700)
            self.created.append(str(self.unit_dir))
        for unit, text in ((self.service, units["service"]), (self.timer, units["timer"])):
            path = self.unit_dir / unit
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
            self.created.append(str(path))
            with os.fdopen(fd, "wb") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
        self._systemctl("daemon-reload")
        self.installed_at = time.time()
        self.floor = self.installed_at
        enabled = self._systemctl("enable", "--runtime", "--now", self.timer)
        for line in (enabled.stdout + enabled.stderr).splitlines():
            match = re.match(r'Created symlink "?([^" ]+)"? ', line)
            if match:
                self.created.append(match[1])
        self.observer = threading.Thread(target=self._observe, daemon=True)
        self.observer.start()
        timer, service = self.show(self.timer), self.show(self.service)
        if (timer["ActiveState"] != "active" or timer["UnitFileState"] != "enabled-runtime"
                or timer["FragmentPath"] != str(self.unit_dir / self.timer) or timer["Triggers"] != self.service
                or service["FragmentPath"] != str(self.unit_dir / self.service)):
            raise TimerError("installed test timer did not load the rendered units")
        return dict(service=self.service, timer=self.timer, unit_dir=str(self.unit_dir),
                    service_sha256=_sha(units["service"]), timer_sha256=_sha(units["timer"]))

    def _idle(self):
        service = self.show(self.service)
        return service["MainPID"] == "0" and service["ControlPID"] == "0" and service["ActiveState"] in {"inactive", "failed"}

    def pause(self, reason):
        """Hold the timer (never the controller) so a fault can be staged before the next tick."""
        self._systemctl("stop", self.timer)
        deadline = time.monotonic() + 180
        while not self._idle():
            if time.monotonic() > deadline:
                raise TimerError("controller invocation did not finish while the timer was held")
            time.sleep(0.25)
        self.collect()
        self.pauses.append(dict(reason=reason, paused_at=time.time()))

    def resume(self):
        # Under the observer lock: a restarted timer may fire at once, never mid-readback.
        with self.lock:
            self.floor = time.time()
            self.pauses[-1]["resumed_at"] = self.floor
            self._systemctl("start", self.timer)

    def barrier(self):
        """From now on only ticks that START later count (e.g. "two further full ticks")."""
        with self.lock:
            self.floor = time.time()

    def caught_up(self):
        """Idle, and every finished tick after the barrier has been consumed."""
        with self.lock:
            found = self.collect()
            return self._idle() and not any(
                key not in self.consumed and value["start"] is not None and value["start"] >= self.floor - 0.5
                for key, value in found.items())

    # ---------------------------------------------------------- observation
    def invocations(self):
        """Every invocation of the test service, from the journal: start/end/stdout."""
        result = subprocess.run(["/usr/bin/journalctl", "--user", "-u", self.service, "-o", "json", "--no-pager",
                                 "--since", "@" + str(int(self.installed_at) - 1)],
                                env=self.env, capture_output=True, text=True, timeout=30, check=True)
        found = {}
        for line in result.stdout.splitlines():
            entry = json.loads(line)
            identifier = entry.get("_SYSTEMD_INVOCATION_ID") or entry.get("USER_INVOCATION_ID")
            if not identifier or not isinstance(entry.get("MESSAGE"), str):
                continue
            at = int(entry["__REALTIME_TIMESTAMP"]) / 1e6
            value = found.setdefault(identifier, dict(invocation_id=identifier, start=None, end=None, stdout=[],
                                                      manager=[]))
            message = entry["MESSAGE"]
            if entry.get("_SYSTEMD_INVOCATION_ID") == identifier and entry.get("_SYSTEMD_USER_UNIT") == self.service:
                value["stdout"].append(message)
            else:
                value["manager"].append(message)
                if message.startswith("Starting "):
                    value["start"] = at
                if message.startswith(("Finished ", "Failed to start ")) or "Failed with result" in message:
                    value["end"] = at
        return found

    def _observe(self):
        while not self.stop.wait(0.2):
            try:
                self.collect()
            except Exception as error:  # keep observing; the oracle sees the gap
                self.observer_errors.append(type(error).__name__)

    def _safe_to_read(self, latest):
        """Idle, and the next OnUnitActiveSec tick (15 s after the latest start) cannot begin during the
        millisecond shared-lock read (0.8 s margin), so the probe never blocks the controller's lock."""
        return self._idle() and time.time() < latest["start"] + 14.2

    def collect(self):
        """Read the durable receipt of the latest finished invocation while it is safe to do so."""
        with self.lock:
            found = self.invocations()
            self.found = found
            pending = [key for key, value in found.items() if value["end"] is not None
                       and key not in self.receipts and key not in self.missed]
            if not pending:
                return found
            latest = max(found.values(), key=lambda value: value["start"] or 0)
            if latest["start"] is None or latest["end"] is None or not self._safe_to_read(latest):
                return found
            try:
                receipt = read_tick(self.state_dir)
            except ValueError:
                return found
            if receipt["phase"] == "completed" and receipt["invocation_id"] == latest["invocation_id"]:
                self.receipts[receipt["invocation_id"]] = receipt
                # Older finished invocations were overwritten by a later tick
                # before readback: record the gap, never invent a receipt.
                self.missed.update(key for key in pending if key != receipt["invocation_id"])
            return found

    def next_tick(self, timeout=240):
        """The oldest not-yet-consumed timer invocation started after the last barrier."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                found = self.collect()
                ready = sorted((value for identifier, value in found.items()
                                if identifier not in self.consumed and value["start"] is not None
                                and value["start"] >= self.floor - 0.5 and value["end"] is not None
                                and (identifier in self.receipts or identifier in self.missed) and value["stdout"]),
                               key=lambda value: value["start"])
                if ready:
                    value = ready[0]
                    self.consumed.append(value["invocation_id"])
                    # stderr shares the stream; the round result is the line with "agents".
                    line = next((text for text in reversed(value["stdout"]) if text.startswith('{"agents"')),
                                value["stdout"][-1])
                    receipt = self.receipts.get(value["invocation_id"])
                    failed = (not receipt["ok"]) if receipt else any(
                        row["errors"] or row["retry_pending"] for row in json.loads(line).get("agents", {}).values())
                    done = subprocess.CompletedProcess(["timer:" + self.timer], int(failed), line, "")
                    return done, value
            time.sleep(0.5)
        raise TimerError("no timer-triggered controller invocation observed")

    def evidence(self):
        found = self.collect()
        return dict(service=self.service, timer=self.timer, commands=self.commands, pauses=self.pauses,
                    consumed=self.consumed, receipts=self.receipts, missed_receipts=sorted(self.missed),
                    observer_errors=self.observer_errors,
                    invocations={key: dict(start=value["start"], end=value["end"],
                                           stdout_lines=len(value["stdout"])) for key, value in found.items()})

    # --------------------------------------------------------------- cleanup
    def cleanup(self):
        """Remove exactly what install() created and read back that both units are gone."""
        self.stop.set()
        if self.observer is not None:
            self.observer.join(timeout=30)
        self._systemctl("disable", "--runtime", "--now", self.timer, check=False)
        self._systemctl("stop", self.service, check=False)
        removed = []
        for path in reversed(self.created):
            item = Path(path)
            if item.is_symlink() or item.is_file():
                item.unlink()
                removed.append(path)
        self._systemctl("daemon-reload", check=False)
        self._systemctl("reset-failed", self.service, check=False)
        for path in reversed(self.created):
            item = Path(path)
            if item.is_dir() and not any(item.iterdir()):
                item.rmdir()
                removed.append(path)
        after = {unit: self.show(unit) for unit in (self.service, self.timer)}
        if any(value["LoadState"] != "not-found" or value["ActiveState"] != "inactive" for value in after.values()):
            raise TimerError("test timer/service still loaded after cleanup")
        return dict(removed=removed, after=after)


def _sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()
