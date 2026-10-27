"""Shared owner-controlled Claude runtime admission for launcher and audit."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

MAX_ENV_BYTES = 4 * 1024 * 1024
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")

class LauncherError(RuntimeError):
    pass

def _trusted_directory_chain(path: Path, uid: int) -> bool:
    for candidate in (path, *path.parents):
        try:
            metadata = candidate.lstat()
        except OSError:
            return False
        mode = stat.S_IMODE(metadata.st_mode)
        sticky = bool(mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, uid}
            or (bool(mode & 0o022) and not sticky)
        ):
            return False
    return True

def _trusted_path(
    raw: str,
    uid: int,
    *,
    directory: bool = False,
    executable: bool = False,
    canonical: bool = False,
    root_owned: bool = False,
) -> Path:
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise LauncherError("trusted path must be absolute and normalized")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as error:
        raise LauncherError("trusted path is unavailable") from error
    if canonical and resolved != path:
        raise LauncherError("trusted path must name its canonical target")
    expected_type = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if (
        not expected_type
        or metadata.st_uid not in ({0} if root_owned else {0, uid})
        or bool(stat.S_IMODE(metadata.st_mode) & 0o022)
        or (executable and not bool(stat.S_IMODE(metadata.st_mode) & 0o111))
        or not _trusted_directory_chain(resolved.parent, uid)
    ):
        raise LauncherError("trusted path owner, mode or type is invalid")
    return resolved

def admit_claude_runtime(agent: str, home: Path, uid: int, raw_wrapper: str, raw_config: str) -> tuple[Path, Path]:
    """Admit only the requested agent's owner-approved exact runtime binding."""
    if not isinstance(agent, str) or AGENT_NAME.fullmatch(agent) is None:
        raise LauncherError("invalid Claude agent")
    wrapper = _trusted_path(raw_wrapper, uid, executable=True)
    config = _trusted_path(raw_config, uid, directory=True, canonical=True)
    binding = home / ".config/buzz/agents" / f"{agent}.claude-runtime.json"
    if not binding.exists() and not binding.is_symlink():
        expected = {"claude-glm": home / ".claude-glm", "claude-buzz": home / ".claude-buzz"}
        if wrapper.name in expected and config == expected[wrapper.name]:
            return wrapper, config
        raise LauncherError("Claude wrapper and config directory disagree")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate binding key")
            result[key] = value
        return result
    def read_bound(path: Path, mode: int | None, limit: int) -> bytes:
        _trusted_path(str(path), uid, canonical=True)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode)
                    or before.st_uid not in ({uid} if mode is not None else {0, uid})
                    or bool(stat.S_IMODE(before.st_mode) & 0o022)
                    or (mode is not None and stat.S_IMODE(before.st_mode) != mode)
                    or (mode is None and not stat.S_IMODE(before.st_mode) & 0o111)
                    or before.st_size > limit):
                raise LauncherError("invalid Claude binding ownership or mode")
            data = b""
            while len(data) <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data += chunk
            after = os.fstat(fd)
            current = path.lstat()
            fields = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if len(data) > limit or len(data) != before.st_size or fields(before) != fields(after) or fields(after) != fields(current):
                raise LauncherError("Claude binding changed during read")
            return data
        finally:
            os.close(fd)
    try:
        value = json.loads(read_bound(binding, 0o600, 65536), object_pairs_hook=unique)
        keys = {"schema_version", "agent", "wrapper", "wrapper_sha256", "config_dir"}
        if not isinstance(value, dict) or set(value) != keys or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("invalid binding shape")
        if any(not isinstance(value[key], str) for key in keys - {"schema_version"}):
            raise ValueError("invalid binding type")
        expected_wrapper = home / ".local/bin" / f"claude-{agent}"
        expected_config = home / f".claude-{agent}"
        if (
            agent in {"glm", "buzz"}
            or value["agent"] != agent
            or raw_wrapper != str(expected_wrapper)
            or wrapper != expected_wrapper
            or config != expected_config
            or raw_config != str(expected_config)
            or value["wrapper"] != raw_wrapper
            or value["config_dir"] != raw_config
        ):
            raise ValueError("binding does not match requested agent")
        metadata = config.lstat()
        if metadata.st_uid != uid or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ValueError("config must be owner-only")
        if SHA256.fullmatch(value["wrapper_sha256"]) is None or hashlib.sha256(read_bound(wrapper, None, MAX_ENV_BYTES)).hexdigest() != value["wrapper_sha256"]:
            raise ValueError("wrapper digest mismatch")
        return wrapper, config
    except (OSError, ValueError, TypeError, RecursionError) as error:
        raise LauncherError("Claude runtime binding is invalid") from error
