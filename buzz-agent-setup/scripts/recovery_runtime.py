"""Host-level proof required before the owner controller may resume a Thread."""
import hashlib
import os
from pathlib import Path
import re
import signal
from agent_recovery import JOURNAL_VERSION


def _identity(pid):
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip(), stat[19], stat[0]


def verify_process(*, snapshot, main_pid, binary_sha256, revision):
    """Verify actual executable and initial env; PID equality alone is not proof."""
    if (type(main_pid) is not int or main_pid <= 1 or snapshot.get("pid") != main_pid
            or not re.fullmatch(r"[0-9a-f]{64}", binary_sha256)
            or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        return False
    try:
        before = _identity(main_pid)
        if before[:2] != (snapshot.get("boot_id"), snapshot.get("process_start_ticks")) or before[2] == "Z":
            return False
        with Path(f"/proc/{main_pid}/exe").open("rb") as exe:
            digest = hashlib.file_digest(exe, "sha256").hexdigest()
        if digest != binary_sha256:
            return False
        with Path(f"/proc/{main_pid}/environ").open("rb") as env:
            data = env.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            return False
        # Never copy/print the rest of the environment (it contains credentials).
        wanted = b"BUZZ_ACP_RECOVERY_REVISION=" + revision.encode()
        if [part for part in data.split(b"\0") if part.startswith(b"BUZZ_ACP_RECOVERY_REVISION=")] != [wanted]:
            return False
        after = _identity(main_pid)
        return after[:2] == before[:2] and after[2] != "Z"
    except (OSError, ValueError, IndexError):
        return False


def request_prewarm(*, snapshot, main_pid, binary_sha256, revision):
    """Wake only the current protocol process, bound by pidfd; never run a Thread.

    Version 3 publishes its journal only after installing SIGUSR1. Both process
    birth/executable/revision and the service's MainPID are rechecked after open.
    There is deliberately no bare-PID or systemctl-kill fallback.
    """
    if type(snapshot.get("version")) is not int or snapshot["version"] != JOURNAL_VERSION or snapshot.get("phase") != "starting":
        return False
    descriptor = None
    try:
        pid = main_pid()
        proof = dict(snapshot=snapshot, main_pid=pid, binary_sha256=binary_sha256, revision=revision)
        if not verify_process(**proof):
            return False
        descriptor = os.pidfd_open(pid, 0)
        if main_pid() != pid or not verify_process(**proof):
            return False
        signal.pidfd_send_signal(descriptor, signal.SIGUSR1, None, 0)
        return True
    except (OSError, ValueError, AttributeError):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
