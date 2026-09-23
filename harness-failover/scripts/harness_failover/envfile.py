"""Agent env file rewriting. Env files hold private keys: only the requested variables are touched, every
other line stays byte-identical, files stay 0600, and any failure rolls the whole batch back."""
from __future__ import annotations

import glob
import os
import re
import shlex
import stat
import subprocess
import tempfile


class EnvError(RuntimeError):
    pass


def _fmt(v: str) -> str:
    return shlex.quote(v) if v else ""


def rewrite_text(text: str, updates: dict) -> str:
    """Replace / append / remove (value None) the given variables; nothing else changes."""
    out, seen = [], set()
    for line in text.splitlines(keepends=True):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            seen.add(key)
            if updates[key] is not None:
                out.append(f"{key}={_fmt(updates[key])}\n")
            continue
        out.append(line)
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    for key, val in updates.items():
        if key not in seen and val is not None:
            out.append(f"{key}={_fmt(val)}\n")
    return "".join(out)


def read_vars(path: str, keys) -> dict:
    """Read only the named (non-secret) variables from an env file."""
    out: dict[str, str] = {}
    with open(path, errors="replace") as f:
        for line in f:
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.rstrip("\n"))
            if m and m.group(1) in keys:
                try:
                    parts = shlex.split(m.group(2))
                except ValueError:
                    parts = [m.group(2)]
                out[m.group(1)] = parts[0] if parts else ""
    return out


def bash_verify(path: str, keys) -> dict:
    """source the file in bash and read back only the named variables."""
    script = 'set -a; . "$1" || exit 3; for k in ' + " ".join(keys) + '; do printf "%s=%s\\n" "$k" "${!k-}"; done'
    # Minimal environment: a caller that happens to export CODEX_HOME & co. must not change what we read back.
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/")}
    p = subprocess.run(["bash", "-c", script, "_", path], capture_output=True, text=True, timeout=20, env=env)
    if p.returncode != 0:
        raise EnvError("env file cannot be sourced by bash")
    return dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)


def _check_env_file(path: str) -> None:
    if os.path.islink(path):
        raise EnvError(f"refusing symlinked env: {os.path.basename(path)}")
    st = os.stat(path)
    if st.st_uid != os.getuid():
        raise EnvError(f"env owner mismatch: {os.path.basename(path)}")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise EnvError(f"env must be 0600: {os.path.basename(path)}")


def apply_updates(paths, updates: dict, ts: str, verify=None) -> list:
    """Rewrite every env file (verified before replacing, backup kept). Returns backup paths.
    On any failure every file already replaced is restored — each restore is attempted even if another fails —
    and no temp copy of the secrets is left behind."""
    verify = verify or bash_verify
    keys = list(updates)
    want = {k: (v or "") for k, v in updates.items()}
    done: list[tuple[str, str]] = []
    try:
        for path in paths:
            _check_env_file(path)
            original = open(path, errors="surrogateescape", newline="").read()  # newline="": keep CRLF as it is
            new = rewrite_text(original, updates)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".env.new.")
            backup, made_backup = f"{path}.bak.{ts}-failover", False
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", errors="surrogateescape", newline="") as f:
                    f.write(new)
                    f.flush()
                    os.fsync(f.fileno())
                if verify(tmp, keys) != want:
                    raise EnvError(f"verification mismatch: {os.path.basename(path)}")
                bfd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                made_backup = True
                with os.fdopen(bfd, "w", errors="surrogateescape", newline="") as f:
                    f.write(original)
                os.replace(tmp, path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                if made_backup and os.path.exists(backup):
                    os.unlink(backup)  # only a backup we created ourselves, and only if the file was not replaced
                raise
            done.append((path, backup))
    except BaseException as exc:
        failed = []
        for path, backup in reversed(done):
            try:
                os.replace(backup, path)
                os.chmod(path, 0o600)
            except OSError:
                failed.append(os.path.basename(path))
        if failed:
            raise EnvError(f"rollback incomplete, restore from the .bak files: {', '.join(failed)}") from exc
        raise
    return [b for _, b in done]


def prune_backups(paths, keep: int = 5) -> int:
    """Each switch leaves a full (0600) copy of every env file; keep only the newest `keep` per file."""
    removed = 0
    for path in paths:
        for old in sorted(glob.glob(f"{glob.escape(path)}.bak.*-failover"))[:-keep] if keep > 0 else []:
            try:
                os.unlink(old)
                removed += 1
            except OSError:
                pass
    return removed
