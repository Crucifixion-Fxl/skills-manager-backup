#!/usr/bin/python3
"""Canonical clean launcher for persistent Buzz ACP agents."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
import struct
import sys
from typing import NoReturn


MAX_ENV_BYTES = 4 * 1024 * 1024
MAX_BINARY_BYTES = 128 * 1024 * 1024
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
ASSIGNMENT = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)")
SHA256 = re.compile(r"[0-9a-f]{64}")
REQUIRED = frozenset(
    {
        "BUZZ_RELAY_URL",
        "BUZZ_PRIVATE_KEY",
        "BUZZ_ACP_AGENT_OWNER",
        "BUZZ_ACP_AGENT_COMMAND",
        "BUZZ_ACP_BINARY",
        "BUZZ_ACP_BINARY_SHA256",
        "BUZZ_ACP_SYSTEM_PROMPT_FILE",
        "BUZZ_RESPONSIBLE_CONFIG",
    }
)
FORBIDDEN = frozenset(
    {
        "BASH_ENV",
        "BASHOPTS",
        "CDPATH",
        "ENV",
        "GCONV_PATH",
        "GLIBC_TUNABLES",
        "GLOBIGNORE",
        "HOME",
        "HOSTALIASES",
        "IFS",
        "LD_PRELOAD",
        "LOGNAME",
        "LOCPATH",
        "MALLOC_TRACE",
        "NLSPATH",
        "NODE_OPTIONS",
        "OLDPWD",
        "PATH",
        "PERL5OPT",
        "PWD",
        "PYTHONHOME",
        "PYTHONPATH",
        "RUBYOPT",
        "RES_OPTIONS",
        "RUN_HOME",
        "RUN_LANG",
        "RUN_LOGNAME",
        "RUN_PATH",
        "RUN_USER",
        "SHELL",
        "SHELLOPTS",
        "SHLVL",
        "USER",
        "TZDIR",
        "_",
    }
)
FORBIDDEN_PREFIXES = ("BASH_FUNC_", "DYLD_", "LD_AUDIT", "LD_LIBRARY_PATH")


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


def _literal(raw: str) -> str:
    if "\x00" in raw or "`" in raw or "$(" in raw or "${" in raw:
        raise LauncherError("env values must be literal")
    lexer = shlex.shlex(raw, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        values = list(lexer)
    except ValueError as error:
        raise LauncherError("env value has invalid quoting") from error
    if not raw and not values:
        return ""
    if len(values) != 1:
        raise LauncherError("env values must contain one literal shell word")
    if "\x00" in values[0]:
        raise LauncherError("env values cannot contain NUL")
    return values[0]


def _read_env(path: Path, uid: int) -> dict[str, str]:
    descriptor: int | None = None
    try:
        if (
            path.resolve(strict=True) != path
            or not _trusted_directory_chain(path.parent, uid)
        ):
            raise LauncherError("env path must be canonical")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != uid
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size > MAX_ENV_BYTES
        ):
            raise LauncherError("env must be an owner-only 0600 regular file")
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise LauncherError("env changed while opening")
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_ENV_BYTES:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_ENV_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        if (
            total > MAX_ENV_BYTES
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or after.st_size != total
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise LauncherError("env changed or exceeded the size limit")
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino):
            raise LauncherError("env path changed while reading")
        try:
            text = b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError as error:
            raise LauncherError("env must be UTF-8") from error
    except OSError as error:
        raise LauncherError("env is unavailable or unsafe") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)

    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(line)
        if match is None:
            raise LauncherError("env contains unsupported syntax")
        key, raw = match.groups()
        if (
            key in values
            or key in FORBIDDEN
            or key.startswith(FORBIDDEN_PREFIXES)
            or key.startswith("buzz_")
        ):
            raise LauncherError("env contains a duplicate or forbidden key")
        values[key] = _literal(raw)
    missing = REQUIRED - set(values)
    if missing:
        raise LauncherError("env is missing required keys")
    return values


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


def _safe_path(raw: str, uid: int) -> str:
    parts: list[str] = []
    for item in raw.split(":"):
        if not item:
            raise LauncherError("safe PATH contains an empty entry")
        resolved = str(_trusted_path(item, uid, directory=True))
        if resolved not in parts:
            parts.append(resolved)
    return ":".join(parts)


def prepare_launch(
    agent: str,
    *,
    home: Path,
    uid: int,
    username: str,
) -> tuple[Path, Path, tuple[str, ...], dict[str, str], str | None]:
    if AGENT_NAME.fullmatch(agent) is None:
        raise LauncherError("invalid agent name")
    home = _trusted_path(str(home), uid, directory=True)
    env = _read_env(home / ".config/buzz/agents" / f"{agent}.env", uid)

    work_root = _trusted_path(str(home / "buzz-agent-work"), uid, directory=True)
    raw_workdir = env.get("AGENT_WORKDIR", str(work_root / agent))
    workdir = _trusted_path(raw_workdir, uid, directory=True)
    if not workdir.is_relative_to(work_root):
        raise LauncherError("agent workdir must stay below the owner work root")
    legacy = workdir / "naturehood"
    if "AGENT_WORKDIR" not in env and (legacy / ".git").exists():
        workdir = _trusted_path(str(legacy), uid, directory=True)
    if not (workdir / ".git").exists():
        raise LauncherError("agent workdir must be a Git checkout")

    raw_path = env.get(
        "BUZZ_AGENT_SAFE_PATH",
        f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin",
    )
    safe_path = _safe_path(raw_path, uid)

    proxy = _trusted_path(env["BUZZ_ACP_AGENT_COMMAND"], uid, executable=True)
    raw_adapter = env.get("BUZZ_ACP_MEDIA_ADAPTER_COMMAND")
    raw_media_cli = env.get("BUZZ_ACP_MEDIA_BUZZ_CLI")
    media_mode = env.get("BUZZ_ACP_MEDIA_MODE")
    if bool(raw_adapter) != bool(raw_media_cli):
        raise LauncherError("media adapter and Buzz CLI must be configured together")
    if raw_adapter is None and media_mode != "stock_text_only":
        raise LauncherError("stock text-only mode must be explicit")
    if raw_adapter is not None and media_mode is not None:
        raise LauncherError("media mode conflicts with the configured proxy")
    adapter = (
        _trusted_path(raw_adapter, uid, executable=True)
        if raw_adapter is not None
        else proxy
    )
    env["BUZZ_ACP_AGENT_COMMAND"] = str(proxy)
    if raw_adapter is not None and raw_media_cli is not None:
        media_cli = _trusted_path(raw_media_cli, uid, executable=True)
        env["BUZZ_ACP_MEDIA_ADAPTER_COMMAND"] = str(adapter)
        env["BUZZ_ACP_MEDIA_BUZZ_CLI"] = str(media_cli)
    if adapter.name == "claude-agent-acp":
        wrapper_raw = env.get("HARNESS_CLAUDE_WRAPPER")
        config_raw = env.get("CLAUDE_CONFIG_DIR")
        if not wrapper_raw or not config_raw:
            raise LauncherError(
                "Claude adapter requires HARNESS_CLAUDE_WRAPPER and CLAUDE_CONFIG_DIR"
            )
        wrapper = _trusted_path(wrapper_raw, uid, executable=True)
        config = _trusted_path(
            config_raw,
            uid,
            directory=True,
            canonical=True,
        )
        expected_config = home / (
            ".claude-glm" if wrapper.name == "claude-glm" else ".claude-buzz"
        )
        if wrapper.name not in {"claude-buzz", "claude-glm"} or config != expected_config:
            raise LauncherError("Claude wrapper and config directory disagree")
        configured = env.get("CLAUDE_CODE_EXECUTABLE")
        if configured and _trusted_path(configured, uid, executable=True) != wrapper:
            raise LauncherError("Claude executable and wrapper disagree")
        env["HARNESS_CLAUDE_WRAPPER"] = str(wrapper)
        env["CLAUDE_CODE_EXECUTABLE"] = str(wrapper)
        env["CLAUDE_CONFIG_DIR"] = str(config)
    elif adapter.name == "codex-acp":
        raw_codex_home = env.get("CODEX_HOME")
        raw_codex_path = env.get("CODEX_PATH")
        if not raw_codex_home or not raw_codex_path:
            raise LauncherError("Codex adapter requires CODEX_HOME and CODEX_PATH")
        codex_home = _trusted_path(raw_codex_home, uid, directory=True)
        codex_path = _trusted_path(
            raw_codex_path,
            uid,
            executable=True,
            canonical=True,
        )
        env["CODEX_HOME"] = str(codex_home)
        env["CODEX_PATH"] = str(codex_path)

    configured_binary = env["BUZZ_ACP_BINARY"]
    expected_digest = env["BUZZ_ACP_BINARY_SHA256"]
    if SHA256.fullmatch(expected_digest) is None:
        raise LauncherError("pinned buzz-acp requires a SHA-256")
    binary = _trusted_path(configured_binary, uid, executable=True, canonical=True)

    launch_env = dict(env)
    launch_env.update(
        {
            "HOME": str(home),
            "USER": username,
            "LOGNAME": username,
            "SHELL": "/bin/bash",
            "PATH": safe_path,
            "LANG": env.get("LANG", "C.UTF-8"),
            "TERM": "dumb",
            "PWD": str(workdir),
        }
    )
    argv = (str(binary), "--relay-url", env["BUZZ_RELAY_URL"])
    return workdir, binary, argv, launch_env, expected_digest


def _open_binary(path: Path, uid: int, expected_digest: str | None) -> int:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, uid}
            or bool(stat.S_IMODE(before.st_mode) & 0o022)
            or not bool(stat.S_IMODE(before.st_mode) & 0o111)
            or before.st_size > MAX_BINARY_BYTES
        ):
            raise LauncherError("buzz-acp binary is unsafe")
        digest = hashlib.sha256()
        total = 0
        magic = b""
        while total <= MAX_BINARY_BYTES:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_BINARY_BYTES + 1 - total))
            if not chunk:
                break
            if not magic:
                magic = chunk[:4]
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        if (
            total > MAX_BINARY_BYTES
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or after.st_size != total
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or magic != b"\x7fELF"
            or (expected_digest is not None and digest.hexdigest() != expected_digest)
        ):
            raise LauncherError("buzz-acp binary changed or failed its digest")
        _validate_elf(descriptor, after.st_size, uid)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _validate_elf(descriptor: int, size: int, uid: int) -> None:
    header = os.pread(descriptor, 64, 0)
    if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01":
        raise LauncherError("buzz-acp must be a 64-bit little-endian ELF")
    try:
        (
            file_type,
            machine,
            version,
            entry_point,
            program_offset,
            _section_offset,
            _flags,
            header_size,
            program_entry_size,
            program_count,
            _section_entry_size,
            _section_count,
            _section_names,
        ) = struct.unpack("<HHIQQQIHHHHHH", header[16:])
    except struct.error as error:
        raise LauncherError("buzz-acp ELF header is invalid") from error
    machine_by_host = {"x86_64": 62, "aarch64": 183}
    expected_machine = machine_by_host.get(os.uname().machine)
    if (
        file_type not in {2, 3}
        or expected_machine is None
        or machine != expected_machine
        or version != 1
        or header_size != 64
        or program_entry_size != 56
        or not 0 < program_count <= 1024
        or program_offset > size
        or program_count * program_entry_size > size - program_offset
    ):
        raise LauncherError("buzz-acp ELF architecture or program table is invalid")
    interpreter: str | None = None
    has_load_segment = False
    entry_in_executable_load = False
    for index in range(program_count):
        entry = os.pread(
            descriptor,
            56,
            program_offset + index * program_entry_size,
        )
        if len(entry) != 56:
            raise LauncherError("buzz-acp ELF program table is truncated")
        kind, flags, offset, virtual, _physical, file_size, memory_size, alignment = (
            struct.unpack("<IIQQQQQQ", entry)
        )
        if kind == 1:
            has_load_segment = True
            if (
                file_size > memory_size
                or offset > size - file_size
                or (
                    alignment not in {0, 1}
                    and (
                        alignment & (alignment - 1)
                        or (offset - virtual) % alignment
                    )
                )
            ):
                raise LauncherError("buzz-acp ELF load segment is invalid")
            if flags & 0x1 and virtual <= entry_point < virtual + memory_size:
                entry_in_executable_load = True
        if kind != 3:
            continue
        if interpreter is not None or file_size < 2 or file_size > 4096 or offset > size - file_size:
            raise LauncherError("buzz-acp ELF interpreter is invalid")
        raw = os.pread(descriptor, file_size, offset)
        if len(raw) != file_size or not raw.endswith(b"\x00") or b"\x00" in raw[:-1]:
            raise LauncherError("buzz-acp ELF interpreter is invalid")
        try:
            interpreter = raw[:-1].decode("utf-8")
        except UnicodeDecodeError as error:
            raise LauncherError("buzz-acp ELF interpreter is invalid") from error
    if interpreter is not None:
        _trusted_path(interpreter, uid, executable=True, root_owned=True)
    if not has_load_segment or not entry_in_executable_load:
        raise LauncherError("buzz-acp ELF entry point is not executable")


def _exec_binary(
    descriptor: int, argv: tuple[str, ...], environment: dict[str, str]
) -> NoReturn:
    # Keep validation and execution bound to the same verified ELF inode.
    os.execve(descriptor, list(argv), environment)
    raise LauncherError("buzz-acp exec unexpectedly returned")


def launch(agent: str) -> NoReturn:
    account = pwd.getpwuid(os.geteuid())
    workdir, binary, argv, environment, expected_digest = prepare_launch(
        agent,
        home=Path(account.pw_dir),
        uid=account.pw_uid,
        username=account.pw_name,
    )
    descriptor = _open_binary(binary, account.pw_uid, expected_digest)
    try:
        os.chdir(workdir)
        _exec_binary(descriptor, argv, environment)
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("run-agent: usage: run-agent.py <agent-name>", file=sys.stderr)
        return 2
    try:
        launch(args[0])
    except (LauncherError, OSError):
        print("run-agent: launch contract failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
