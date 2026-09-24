#!/usr/bin/python3 -I
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Audit one host's local Buzz runtime against one immutable Skills revision.

P1 is deliberately offline and read-only.  It discovers the deployed topology
from user systemd units, then validates only metadata, key names, paths,
permissions, configuration shape and revision pins.  Secret values and pubkeys
are never included in the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import tomllib
from typing import Any, Sequence
import urllib.parse


SHA40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TOKEN_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
RELEASE_REF = re.compile(r"/releases/([0-9a-f]{40})(?:/|$)")
AGENT_UNIT = re.compile(r"buzz-local-([A-Za-z0-9][A-Za-z0-9_-]*)\.service")
SYNC_UNIT = re.compile(r"gitlab-buzz-sync-([A-Za-z0-9][A-Za-z0-9_-]*)\.service")
FEISHU_UNIT = re.compile(r"buzz-feishu-([A-Za-z0-9][A-Za-z0-9_-]*)\.service")
TODO_UNIT = re.compile(r"gitlab-todo-sync-([A-Za-z0-9][A-Za-z0-9_-]*)\.service")
JOIN_UNIT = re.compile(r"(buzz-agent-join)\.service")
FORBIDDEN_ENV_KEYS = frozenset(
    {
        "AMAP_MAPS_API_KEY",
        "BASH_ENV",
        "BASHOPTS",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
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
        "TZDIR",
        "USER",
        "_",
    }
)
FORBIDDEN_ENV_PREFIXES = ("BASH_FUNC_", "DYLD_", "LD_AUDIT", "LD_LIBRARY_PATH")
RESPONSIBLE_KEYS = frozenset(
    {
        "version",
        "sender_pubkey",
        "state_dir",
        "gitlab",
        "channels",
        "people_file",
        "buzz",
    }
)
BUZZ_KEYS = frozenset({"cli_path", "cli_sha256"})
BUZZ_CLI_RELEASE_DIR = "buzz-0.5.23"
RESPONSIBLE_GITLAB_KEYS = frozenset({"base_url", "token_env", "projects"})
RESPONSIBLE_CHILD_ENV_KEYS = frozenset(
    {
        "HOME",
        "USER",
        "LOGNAME",
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "BUZZ_RELAY_URL",
        "BUZZ_PRIVATE_KEY",
        "BUZZ_AUTH_TAG",
    }
)
CANONICAL_SKILLS_REMOTE = (
    "git@" + ".".join(("gitlab", "addx", "ai")) + ":engineering/skills.git"
)
REQUIRED_AGENT_ENV_KEYS = frozenset(
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
PROMPT_CONTRACT = Path(__file__).resolve().parents[1] / "references/agent-prompt-contract.md"
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_BUZZ_BINARY_BYTES = 128 * 1024 * 1024
MAX_DISCOVERY_ENTRIES = 512
MAX_RELEASE_FILES = 8192
MAX_RELEASE_ENTRIES = 16384
MAX_RELEASE_FILE_BYTES = 32 * 1024 * 1024
MAX_RELEASE_BYTES = 256 * 1024 * 1024
RELEASE_MANIFEST = ".release-manifest.json"
GAP_IDS = tuple(f"LA-{index:02d}" for index in range(1, 13))
AUDIT_FORBIDDEN_PARENT_ENV = frozenset(
    {
        "LD_PRELOAD",
        "LD_AUDIT",
        "LD_LIBRARY_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONINSPECT",
        "PYTHONSTARTUP",
        "BASH_ENV",
        "ENV",
        "GCONV_PATH",
        "GLIBC_TUNABLES",
        "HOSTALIASES",
        "LOCPATH",
        "MALLOC_TRACE",
        "NLSPATH",
        "RES_OPTIONS",
        "TZDIR",
    }
)
SYSTEMD_UNSET_ENVIRONMENT = (
    "LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH PYTHONHOME PYTHONPATH PYTHONINSPECT "
    "PYTHONSTARTUP BASH_ENV ENV NODE_OPTIONS PERL5OPT RUBYOPT GLIBC_TUNABLES "
    "GCONV_PATH LOCPATH NLSPATH MALLOC_TRACE RES_OPTIONS HOSTALIASES TZDIR"
)
PROMPT_BLOCK = re.compile(
    r"<!-- prompt-contract:([a-z-]+):(required|forbidden) -->\s*"
    r"```text\n(.*?)\n```",
    re.DOTALL,
)
KNOWN_PROMPT_ROLES = frozenset(
    {
        "common",
        "generic",
        "desk",
        "platform-desk",
        "dev",
        "bi",
        "investigator",
        "feature",
        "bug",
        "debt",
        "sre",
        "qa",
        "executor",
    }
)
JOIN_MANAGED_PROMPT_ROLES = KNOWN_PROMPT_ROLES - {
    "common",
    "platform-desk",
    "executor",
}


def limit_child_output() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_TEXT_BYTES, MAX_TEXT_BYTES))


def run_bounded_text(
    arguments: list[str],
    *,
    env: dict[str, str],
    timeout: int = 30,
) -> tuple[subprocess.CompletedProcess[bytes], str]:
    with tempfile.TemporaryFile() as output:
        completed = subprocess.run(
            arguments,
            env=env,
            stdout=output,
            stderr=subprocess.DEVNULL,
            preexec_fn=limit_child_output,
            timeout=timeout,
            check=False,
        )
        # unittest mocks may supply stdout directly instead of writing the fd.
        if isinstance(completed.stdout, str):
            text = completed.stdout
        else:
            output.seek(0)
            payload = output.read(MAX_TEXT_BYTES + 1)
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                raise OSError("child output is not UTF-8") from error
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise OSError("child output is oversized")
    return completed, text


def run_bounded_bytes(
    arguments: list[str],
    *,
    env: dict[str, str],
    timeout: int = 30,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], bytes]:
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            arguments,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.DEVNULL,
            preexec_fn=limit_child_output,
            start_new_session=True,
        )
        try:
            process.communicate(input=input_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        finally:
            output.seek(0)
            payload = output.read(MAX_TEXT_BYTES + 1)
    if len(payload) > MAX_TEXT_BYTES:
        raise OSError("child output is oversized")
    return subprocess.CompletedProcess(arguments, process.returncode), payload


def load_prompt_contract() -> dict[str, dict[str, list[str]]]:
    try:
        text = PROMPT_CONTRACT.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("prompt contract SSOT is not readable UTF-8") from exc
    contract: dict[str, dict[str, list[str]]] = {}
    for role, kind, body in PROMPT_BLOCK.findall(text):
        entries = [line.strip() for line in body.splitlines() if line.strip()]
        if not entries or role not in KNOWN_PROMPT_ROLES:
            raise ValueError("prompt contract SSOT block is invalid")
        slot = contract.setdefault(role, {"required": [], "forbidden": []})
        if slot[kind]:
            raise ValueError("prompt contract SSOT block is duplicated")
        slot[kind] = entries
    if set(contract) != KNOWN_PROMPT_ROLES or any(
        not contract[role]["required"] for role in KNOWN_PROMPT_ROLES
    ):
        raise ValueError("prompt contract SSOT does not define every role")
    return contract


def trusted_directory_chain(path: Path, uid: int) -> bool:
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


def read_immutable_file(
    path: Path,
    max_bytes: int,
    *,
    allow_owner_write: bool = False,
) -> tuple[int, bytes]:
    """Read one trusted owner/root file from the same inode that was checked."""
    descriptor: int | None = None
    try:
        if path.resolve(strict=True) != path:
            raise OSError("non-canonical path")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or bool(
                stat.S_IMODE(before.st_mode)
                & (0o022 if allow_owner_write else 0o222)
            )
            or before.st_size > max_bytes
            or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
            or not trusted_directory_chain(path.parent, os.geteuid())
        ):
            raise OSError("unsafe immutable file")
        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            total > max_bytes
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or after.st_size != total
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise OSError("immutable file changed while reading")
        return stat.S_IMODE(after.st_mode), b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)


class Auditor:
    def __init__(
        self,
        home: Path,
        expected_sha: str,
        runtime_dir: Path | None = None,
        managed_settings_root: Path = Path("/etc/claude-code"),
    ) -> None:
        self.home = home
        self.expected_sha = expected_sha
        self.runtime_dir = runtime_dir
        self.managed_settings_root = managed_settings_root
        self.agents_dir = home / ".config/buzz/agents"
        self.units_dir = home / ".config/systemd/user"
        self.release_root = home / ".local/share/buzz-agent-setup/releases"
        self.checks: list[dict[str, str]] = []
        self.harnesses: set[tuple[str, str]] = set()
        self.cli_paths: set[Path] = set()
        self.prompt_contract = load_prompt_contract()
        self.prompt_roles: dict[str, str] = {}
        self._unit_search_cache: list[Path] | None = None
        self._unit_search_failed = False
        self._release_manifest_cache: dict[Path, bool] = {}
        self._release_records_cache: dict[Path, dict[str, dict[str, object]]] = {}
        self._verify_user_manager = self.home == Path.home().resolve()

    @staticmethod
    def gap_ids_for(category: str, subject: str) -> list[str]:
        mapping = {
            "inventory": ["LA-01"],
            "agent_unit": ["LA-01"],
            "agent_env": ["LA-02", "LA-11"],
            "agent_prompt": ["LA-03", "LA-06"],
            "responsible_config": ["LA-04"],
            "sandbox": ["LA-05"],
            "sync_release": ["LA-06", "LA-07"],
            "feishu_release": ["LA-06", "LA-08"],
            "todo_release": ["LA-06", "LA-09"],
            "join_release": ["LA-06", "LA-10"],
            "buzz_cli": ["LA-11"],
            "media_proxy": ["LA-06", "LA-11"],
            "plugin_revision": ["LA-06", "LA-12"],
            "release_integrity": ["LA-06"],
        }
        gaps = list(mapping.get(category, []))
        if category == "inventory":
            if (
                subject in {"systemd", "transient_systemd"}
                or subject.startswith("systemd-lookup:")
            ):
                gaps.extend(("LA-07", "LA-08", "LA-09", "LA-10"))
            elif "gitlab-buzz-sync-" in subject:
                gaps.append("LA-07")
            if "buzz-feishu-" in subject:
                gaps.append("LA-08")
            if "gitlab-todo-sync-" in subject:
                gaps.append("LA-09")
            if "buzz-agent-join" in subject:
                gaps.append("LA-10")
        if category == "shared_launcher":
            if "sync" in subject:
                gaps.extend(("LA-06", "LA-07"))
            elif "todo" in subject:
                gaps.extend(("LA-06", "LA-09"))
            else:
                gaps.append("LA-01")
        return sorted(set(gaps))

    def add(
        self,
        category: str,
        subject: str,
        status: str,
        code: str,
        detail: str = "",
    ) -> None:
        item = {
            "category": category,
            "subject": subject,
            "status": status,
            "code": code,
            "gap_ids": self.gap_ids_for(category, subject),
        }
        if detail:
            item["detail"] = detail
        self.checks.append(item)

    def pass_(self, category: str, subject: str, code: str = "ok") -> None:
        self.add(category, subject, "pass", code)

    def fail(self, category: str, subject: str, code: str, detail: str = "") -> None:
        self.add(category, subject, "fail", code, detail)

    def unknown(self, category: str, subject: str, code: str) -> None:
        self.add(category, subject, "unknown", code)

    def private_file(self, path: Path, category: str, subject: str) -> bool:
        try:
            metadata = path.lstat()
        except OSError:
            self.fail(category, subject, "missing_private_file")
            return False
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            self.fail(category, subject, "not_regular_or_symlink")
            return False
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            self.fail(category, subject, "mode_not_0600")
            return False
        if metadata.st_uid != os.geteuid():
            self.fail(category, subject, "wrong_owner")
            return False
        return True

    def read_text(
        self,
        path: Path,
        category: str,
        subject: str,
        *,
        require_private: bool = False,
        require_trusted: bool = False,
    ) -> str | None:
        descriptor: int | None = None
        try:
            if path.resolve(strict=True) != path:
                raise OSError("non-canonical path")
            if not trusted_directory_chain(path.parent, os.geteuid()):
                raise OSError("untrusted ancestor directory")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_TEXT_BYTES:
                raise OSError("unsafe or oversized file")
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                raise OSError("path changed while opening")
            if require_private and (
                before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600
            ):
                raise OSError("private file owner or mode mismatch")
            if require_trusted and (
                before.st_uid not in {0, os.geteuid()}
                or bool(stat.S_IMODE(before.st_mode) & 0o022)
            ):
                raise OSError("trusted file owner or mode mismatch")
            chunks: list[bytes] = []
            total = 0
            while total <= MAX_TEXT_BYTES:
                chunk = os.read(descriptor, min(1024 * 1024, MAX_TEXT_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            after = os.fstat(descriptor)
            stable = (
                before.st_dev == after.st_dev
                and before.st_ino == after.st_ino
                and before.st_size == after.st_size == total
                and before.st_mtime_ns == after.st_mtime_ns
                and before.st_ctime_ns == after.st_ctime_ns
            )
            current = path.lstat()
            stable = stable and (current.st_dev, current.st_ino) == (
                after.st_dev,
                after.st_ino,
            )
            if total > MAX_TEXT_BYTES or not stable:
                raise OSError("file changed or exceeded limit")
            return b"".join(chunks).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            self.fail(category, subject, "unsafe_unreadable_or_oversized_file")
            return None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def read_json(
        self,
        path: Path,
        category: str,
        subject: str,
        *,
        require_private: bool = False,
        require_trusted: bool = False,
    ) -> dict[str, Any] | None:
        text = self.read_text(
            path,
            category,
            subject,
            require_private=require_private,
            require_trusted=require_trusted,
        )
        if text is None:
            return None
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, RecursionError):
            self.fail(category, subject, "invalid_json")
            return None
        if not isinstance(value, dict):
            self.fail(category, subject, "json_not_object")
            return None
        return value

    def validate_responsible_value(self, value: dict[str, Any]) -> bool:
        try:
            sender = value.get("sender_pubkey")
            state_dir = Path(value.get("state_dir", ""))
            people_file = Path(value.get("people_file", ""))
            gitlab = value.get("gitlab")
            channels = value.get("channels")
            buzz = value.get("buzz")
            if (
                set(value) != RESPONSIBLE_KEYS
                or value.get("version") != 2
                or not isinstance(sender, str)
                or HEX64.fullmatch(sender) is None
                or not state_dir.is_absolute()
                or ".." in state_dir.parts
                or not people_file.is_absolute()
                or ".." in people_file.parts
                or not isinstance(gitlab, dict)
                or set(gitlab) != RESPONSIBLE_GITLAB_KEYS
                or not isinstance(channels, list)
                or not channels
                or len(channels) != len(set(channels))
                or not all(isinstance(item, str) and UUID.fullmatch(item) for item in channels)
                or not isinstance(buzz, dict)
                or set(buzz) != BUZZ_KEYS
                or not isinstance(buzz.get("cli_path"), str)
                or not Path(buzz["cli_path"]).is_absolute()
                or not isinstance(buzz.get("cli_sha256"), str)
                or HEX64.fullmatch(buzz["cli_sha256"]) is None
            ):
                return False
            token_env = gitlab.get("token_env")
            projects = gitlab.get("projects")
            if (
                not isinstance(token_env, str)
                or TOKEN_ENV.fullmatch(token_env) is None
                or token_env.startswith("BUZZ_")
                or token_env in RESPONSIBLE_CHILD_ENV_KEYS
                or not isinstance(projects, list)
                or not projects
                or len(projects) != len(set(projects))
                or not all(type(item) is int and item > 0 for item in projects)
            ):
                return False
            raw_origin = gitlab.get("base_url")
            if not isinstance(raw_origin, str) or not raw_origin:
                return False
            parsed = urllib.parse.urlsplit(raw_origin)
            parsed.port
            if (
                parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or not (
                    parsed.scheme == "https"
                    or (
                        parsed.scheme == "http"
                        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                    )
                )
            ):
                return False
            for path in (state_dir, people_file):
                if (path.exists() or path.is_symlink()) and path.resolve(strict=True) != path:
                    return False
        except (OSError, TypeError, ValueError):
            return False
        return True

    @staticmethod
    def parse_codex_plugin_config(text: str) -> dict[str, dict[str, Any]] | None:
        try:
            value = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, RecursionError):
            return None
        marketplaces = value.get("marketplaces")
        plugins = value.get("plugins")
        marketplace = marketplaces.get("addx") if isinstance(marketplaces, dict) else None
        plugin = plugins.get("addx@addx") if isinstance(plugins, dict) else None
        if not isinstance(marketplace, dict) or not isinstance(plugin, dict):
            return None
        if not set(marketplace) <= {"source_type", "source", "ref"} or set(plugin) != {"enabled"}:
            return None
        return {
            "marketplaces.addx": marketplace,
            'plugins."addx@addx"': plugin,
        }

    def regular_file(
        self,
        path: Path,
        category: str,
        subject: str,
        code: str,
    ) -> bool:
        try:
            metadata = path.lstat()
        except OSError:
            self.fail(category, subject, code)
            return False
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            self.fail(category, subject, code)
            return False
        if (
            metadata.st_uid not in {0, os.geteuid()}
            or bool(stat.S_IMODE(metadata.st_mode) & 0o022)
        ):
            self.fail(category, subject, code)
            return False
        return True

    def owner_executable(
        self,
        path: Path,
        category: str,
        subject: str,
        code: str,
    ) -> bool:
        try:
            metadata = path.lstat()
        except OSError:
            self.fail(category, subject, code)
            return False
        good = (
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid in {0, os.geteuid()}
            and bool(stat.S_IMODE(metadata.st_mode) & 0o111)
            and not bool(stat.S_IMODE(metadata.st_mode) & 0o022)
        )
        if not good:
            self.fail(category, subject, code)
        return good

    @staticmethod
    def trusted_private_directory(path: Path) -> bool:
        try:
            metadata = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError:
            return False
        return bool(
            resolved == path
            and stat.S_ISDIR(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and not bool(stat.S_IMODE(metadata.st_mode) & 0o077)
            and trusted_directory_chain(resolved.parent, os.geteuid())
        )

    def unit_search_directories(self) -> list[Path]:
        if self._unit_search_cache is not None:
            return self._unit_search_cache
        directories = [
            self.units_dir,
            self.home / ".config/systemd/user.control",
            self.home / ".local/share/systemd/user",
        ]
        if self._verify_user_manager:
            child_env = {
                "HOME": str(self.home),
                "PATH": "/usr/bin:/bin",
            }
            if self.runtime_dir is not None:
                child_env["XDG_RUNTIME_DIR"] = str(self.runtime_dir)
            try:
                done, output = run_bounded_text(
                    ["/usr/bin/systemd-analyze", "--user", "unit-paths"],
                    env=child_env,
                    timeout=30,
                )
                values = [Path(line) for line in output.splitlines()]
                if (
                    done.returncode != 0
                    or not values
                    or len(values) > MAX_DISCOVERY_ENTRIES
                    or any(not path.is_absolute() or ".." in path.parts for path in values)
                ):
                    raise OSError("unit path discovery failed")
                directories = values
            except (OSError, subprocess.TimeoutExpired):
                if not self._unit_search_failed:
                    self.unknown("inventory", "systemd", "effective_unit_paths_unavailable")
                    self._unit_search_failed = True
        self._unit_search_cache = list(dict.fromkeys(directories))
        return self._unit_search_cache

    def verify_effective_unit(self, path: Path, category: str, subject: str) -> bool:
        if not self._verify_user_manager:
            return True
        child_env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        if self.runtime_dir is not None:
            child_env["XDG_RUNTIME_DIR"] = str(self.runtime_dir)
        try:
            done, output = run_bounded_text(
                [
                    "/usr/bin/systemctl",
                    "--user",
                    "show",
                    path.name,
                    "--property=FragmentPath",
                    "--property=DropInPaths",
                    "--property=NeedDaemonReload",
                    "--property=ExecStart",
                    "--property=UMask",
                    "--property=NoNewPrivileges",
                    "--property=UnsetEnvironment",
                    "--property=Restart",
                    "--property=RestartUSec",
                ],
                env=child_env,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            done = None
        fields: dict[str, str] = {}
        if done is not None and done.returncode == 0:
            for line in output.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    fields[key] = value
        expected_exec: str | None = None
        expected: dict[str, str] = {}
        try:
            text = path.read_text(encoding="utf-8")
            values = self.unit_values(text, "Service", "ExecStart")
            if len(values) == 1:
                expected_exec = values[0].replace("%h", str(self.home))
            for disk_key, manager_key in (
                ("UMask", "UMask"),
                ("NoNewPrivileges", "NoNewPrivileges"),
                ("UnsetEnvironment", "UnsetEnvironment"),
                ("Restart", "Restart"),
            ):
                values = self.unit_values(text, "Service", disk_key)
                if len(values) == 1:
                    expected[manager_key] = values[0]
            restart_sec = self.unit_values(text, "Service", "RestartSec")
            if len(restart_sec) == 1:
                expected["RestartUSec"] = restart_sec[0]
        except (OSError, UnicodeDecodeError):
            text = ""
        exec_effective = fields.get("ExecStart", "")
        exec_good = expected_exec is None or f"argv[]={expected_exec} ;" in exec_effective
        values_good = all(fields.get(key) == value for key, value in expected.items())
        if (
            fields.get("FragmentPath") != str(path)
            or fields.get("DropInPaths")
            or fields.get("NeedDaemonReload") != "no"
            or not exec_good
            or not values_good
        ):
            self.unknown(category, subject, "effective_unit_surface_unverified")
            return False
        return True

    def reject_unit_dropins(self, path: Path, category: str, subject: str) -> bool:
        if not self.verify_effective_unit(path, category, subject):
            return False
        stem, suffix = path.name.rsplit(".", 1)
        names = [f"{path.name}.d", f"{suffix}.d"]
        parts = stem.split("-")
        for index in range(1, len(parts)):
            names.append(f"{'-'.join(parts[:index])}-.{suffix}.d")
        directories = self.unit_search_directories()
        for root in directories:
            candidate = root / path.name
            if candidate == path:
                continue
            if candidate.exists() or candidate.is_symlink():
                self.unknown(category, subject, "unit_shadow_candidate_not_inspected")
                return False
        scanned = 0
        for root in directories:
            for name in names:
                directory = root / name
                if not directory.exists() and not directory.is_symlink():
                    continue
                try:
                    metadata = directory.lstat()
                    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                        raise OSError("unsafe drop-in directory")
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            scanned += 1
                            if scanned > MAX_DISCOVERY_ENTRIES:
                                raise OSError("too many drop-ins")
                            if entry.name.endswith(".conf"):
                                self.unknown(category, subject, "unit_dropins_not_inspected")
                                return False
                except OSError:
                    self.unknown(category, subject, "unit_dropins_unreadable")
                    return False
        return True

    @staticmethod
    def unit_values(text: str, section: str, key: str) -> list[str]:
        current = ""
        values: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                continue
            if current == section and "=" in line:
                item_key, item_value = line.split("=", 1)
                if item_key.strip() == key:
                    values.append(item_value.strip())
        return values

    @staticmethod
    def unit_keys(text: str, section: str) -> list[str]:
        current = ""
        keys: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                continue
            if current == section and "=" in line:
                item_key, _ = line.split("=", 1)
                keys.append(item_key.strip())
        return keys

    @staticmethod
    def unit_sections(text: str) -> set[str]:
        return {
            line[1:-1].strip()
            for raw in text.splitlines()
            if (line := raw.strip()).startswith("[") and line.endswith("]")
        }

    def unit_exec(
        self,
        path: Path,
        category: str,
        subject: str,
        *,
        forbid_inline_environment: bool = False,
    ) -> str | None:
        text = self.read_text(path, category, subject, require_trusted=True)
        if text is None:
            return None
        values = self.unit_values(text, "Service", "ExecStart")
        if len(values) != 1:
            self.fail(category, subject, "execstart_count_invalid")
            return None
        if forbid_inline_environment and any(
            self.unit_values(text, "Service", "Environment")
            or self.unit_values(text, "Service", "EnvironmentFile")
        ):
            self.fail(category, subject, "inline_environment_forbidden")
            return None
        return values[0].replace("%h", str(self.home))

    def unit_environment(self, text: str, key: str) -> str | None:
        values = self.unit_values(text, "Service", "Environment")
        if len(values) != 1 or self.unit_values(text, "Service", "EnvironmentFile"):
            return None
        try:
            assignments = shlex.split(values[0], posix=True)
        except ValueError:
            return None
        if len(assignments) != 1 or "=" not in assignments[0]:
            return None
        actual_key, value = assignments[0].split("=", 1)
        if actual_key != key or not value:
            return None
        return value.replace("%h", str(self.home))

    def trusted_path_list(self, raw: str) -> bool:
        parts = raw.split(":")
        if not parts:
            return False
        for item in parts:
            path = Path(item)
            try:
                resolved = path.resolve(strict=True)
                metadata = resolved.lstat()
            except OSError:
                return False
            if (
                not item
                or not path.is_absolute()
                or ".." in path.parts
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in {0, os.geteuid()}
                or bool(stat.S_IMODE(metadata.st_mode) & 0o022)
                or not trusted_directory_chain(resolved.parent, os.geteuid())
            ):
                return False
        return True

    def parse_env(self, path: Path, subject: str) -> dict[str, str] | None:
        text = self.read_text(
            path,
            "agent_env",
            subject,
            require_private=True,
        )
        if text is None:
            return None
        values: dict[str, str] = {}
        malformed = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = ASSIGNMENT.match(line)
            if match is None:
                malformed = True
                continue
            key, raw = match.groups()
            if key in values:
                malformed = True
            values[key] = raw
            if not self.literal_shell_value(raw):
                malformed = True
        if malformed:
            self.fail("agent_env", subject, "unsupported_env_syntax")
        required = set(REQUIRED_AGENT_ENV_KEYS)
        if subject.startswith("agent:"):
            agent = subject.removeprefix("agent:")
            if self.prompt_roles.get(agent) in JOIN_MANAGED_PROMPT_ROLES:
                required.add("BUZZ_ACP_CHANNELS")
        missing = sorted(required - set(values))
        if missing:
            self.fail("agent_env", subject, "required_keys_missing", ",".join(missing))
        forbidden = sorted(
            key
            for key in values
            if key in FORBIDDEN_ENV_KEYS
            or key.startswith("buzz_")
            or key.startswith(FORBIDDEN_ENV_PREFIXES)
        )
        if forbidden:
            self.fail(
                "agent_env",
                subject,
                "forbidden_keys",
                f"count={len(forbidden)}",
            )
        if not malformed and not missing and not forbidden:
            self.pass_("agent_env", subject)
        return values

    @staticmethod
    def literal_shell_value(raw: str) -> bool:
        if "\x00" in raw or "`" in raw or "$(" in raw or "${" in raw:
            return False
        lexer = shlex.shlex(raw, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            tokens = list(lexer)
        except ValueError:
            return False
        return (not raw and not tokens) or len(tokens) == 1

    @staticmethod
    def assignment_value(raw: str | None) -> str | None:
        if raw is None or "\x00" in raw or any(token in raw for token in ("$(", "`", "${")):
            return None
        if raw == "":
            return ""
        try:
            values = shlex.split(raw, posix=True)
        except ValueError:
            return None
        if len(values) != 1:
            return None
        return values[0]

    def resolve_path(self, raw: str | None) -> Path | None:
        value = self.assignment_value(raw)
        if value is None:
            return None
        value = value.replace("$HOME", str(self.home)).replace("${HOME}", str(self.home))
        if value.startswith("~/"):
            value = str(self.home / value[2:])
        path = Path(value)
        return path if path.is_absolute() else None

    def validate_release_manifest(self, path: Path) -> bool:
        cached = self._release_manifest_cache.get(path)
        if cached is not None:
            return cached
        valid = False
        manifest = path / RELEASE_MANIFEST
        try:
            root_meta = path.lstat()
            manifest_meta = manifest.lstat()
            if (
                path.resolve(strict=True) != path
                or stat.S_ISLNK(root_meta.st_mode)
                or not stat.S_ISDIR(root_meta.st_mode)
                or root_meta.st_uid not in {0, os.geteuid()}
                or bool(stat.S_IMODE(root_meta.st_mode) & 0o222)
                or stat.S_ISLNK(manifest_meta.st_mode)
                or not stat.S_ISREG(manifest_meta.st_mode)
                or manifest_meta.st_size > MAX_TEXT_BYTES
                or manifest_meta.st_uid not in {0, os.geteuid()}
                or bool(stat.S_IMODE(manifest_meta.st_mode) & 0o222)
            ):
                raise ValueError("unsafe release or manifest")
            _manifest_mode, manifest_bytes = read_immutable_file(manifest, MAX_TEXT_BYTES)
            payload = json.loads(manifest_bytes.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("release manifest is not an object")
            records = payload.get("files")
            if (
                set(payload) != {"version", "commit", "files"}
                or payload.get("version") != 1
                or payload.get("commit") != self.expected_sha
                or not isinstance(records, dict)
                or not records
                or len(records) > MAX_RELEASE_FILES
            ):
                raise ValueError("invalid release manifest schema")
            actual: dict[str, tuple[int, str]] = {}
            total_bytes = 0
            scanned = 0
            for candidate in path.rglob("*"):
                scanned += 1
                if scanned > MAX_RELEASE_ENTRIES:
                    raise ValueError("too many release entries")
                relative = candidate.relative_to(path).as_posix()
                if relative == RELEASE_MANIFEST:
                    continue
                metadata = candidate.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    raise ValueError("release symlink")
                if stat.S_ISDIR(metadata.st_mode):
                    if (
                        metadata.st_uid not in {0, os.geteuid()}
                        or bool(stat.S_IMODE(metadata.st_mode) & 0o222)
                    ):
                        raise ValueError("unsafe release directory")
                    continue
                mode, contents = read_immutable_file(candidate, MAX_RELEASE_FILE_BYTES)
                total_bytes += len(contents)
                if total_bytes > MAX_RELEASE_BYTES:
                    raise ValueError("release is too large")
                actual[relative] = (mode, hashlib.sha256(contents).hexdigest())
                if len(actual) > MAX_RELEASE_FILES:
                    raise ValueError("too many release files")
            if set(records) != set(actual):
                raise ValueError("release file set mismatch")
            for relative, record in records.items():
                relative_path = Path(relative)
                if (
                    not isinstance(relative, str)
                    or not relative
                    or relative_path.is_absolute()
                    or ".." in relative_path.parts
                    or not isinstance(record, dict)
                    or set(record) != {"mode", "sha256"}
                    or not isinstance(record.get("mode"), int)
                    or not isinstance(record.get("sha256"), str)
                    or HEX64.fullmatch(record["sha256"]) is None
                    or actual[relative] != (record["mode"], record["sha256"])
                ):
                    raise ValueError("release record mismatch")
            valid = True
            self._release_records_cache[path] = records
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
            valid = False
        self._release_manifest_cache[path] = valid
        return valid

    def validate_installed_skill_tree(self, path: Path) -> bool:
        release = self.release_root / self.expected_sha
        if not self.validate_release_manifest(release):
            return False
        expected = self._release_records_cache.get(release)
        if not isinstance(expected, dict):
            return False
        try:
            if path.resolve(strict=True) != path:
                raise OSError("installed Skill path is not canonical")
            root_meta = path.lstat()
            if (
                not stat.S_ISDIR(root_meta.st_mode)
                or not trusted_directory_chain(path, os.geteuid())
            ):
                raise OSError("installed Skill root is unsafe")
            actual: dict[str, tuple[int, str]] = {}
            total = 0
            scanned = 0
            for candidate in path.rglob("*"):
                scanned += 1
                if scanned > MAX_RELEASE_ENTRIES:
                    raise OSError("installed Skill has too many entries")
                metadata = candidate.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    raise OSError("installed Skill contains a symlink")
                if stat.S_ISDIR(metadata.st_mode):
                    if not trusted_directory_chain(candidate, os.geteuid()):
                        raise OSError("installed Skill directory is unsafe")
                    continue
                mode, contents = read_immutable_file(
                    candidate,
                    MAX_RELEASE_FILE_BYTES,
                    allow_owner_write=True,
                )
                total += len(contents)
                if total > MAX_RELEASE_BYTES:
                    raise OSError("installed Skill is too large")
                actual[candidate.relative_to(path).as_posix()] = (
                    mode & 0o111,
                    hashlib.sha256(contents).hexdigest(),
                )
                if len(actual) > MAX_RELEASE_FILES:
                    raise OSError("installed Skill has too many files")
            if set(actual) != set(expected):
                return False
            return all(
                isinstance(record, dict)
                and isinstance(record.get("mode"), int)
                and isinstance(record.get("sha256"), str)
                and actual[relative]
                == (record["mode"] & 0o111, record["sha256"])
                for relative, record in expected.items()
            )
        except (OSError, ValueError):
            return False

    def validate_release(self, path: Path, category: str, subject: str, script: str) -> bool:
        expected_path = self.release_root / self.expected_sha
        if path != expected_path:
            self.fail(category, subject, "revision_mismatch")
            return False
        if not self.validate_release_manifest(path):
            self.fail(category, subject, "release_manifest_invalid")
            return False
        try:
            if path.resolve(strict=True) != path:
                raise OSError("release path is not canonical")
            metadata = path.lstat()
            scripts_meta = (path / "scripts").lstat()
            entrypoint = path / "scripts" / script
            script_meta = entrypoint.lstat()
        except OSError:
            self.fail(category, subject, "release_or_entrypoint_missing")
            return False
        directories = (metadata, scripts_meta)
        if any(
            stat.S_ISLNK(item.st_mode)
            or not stat.S_ISDIR(item.st_mode)
            or item.st_uid not in {0, os.geteuid()}
            for item in directories
        ):
            self.fail(category, subject, "release_not_real_directory")
            return False
        if any(stat.S_IMODE(item.st_mode) & 0o222 for item in directories):
            self.fail(category, subject, "release_directory_writable")
            return False
        if (
            stat.S_ISLNK(script_meta.st_mode)
            or not stat.S_ISREG(script_meta.st_mode)
            or script_meta.st_uid not in {0, os.geteuid()}
            or bool(stat.S_IMODE(script_meta.st_mode) & 0o222)
        ):
            self.fail(category, subject, "release_entrypoint_invalid")
            return False
        self.pass_(category, subject)
        return True

    def load_prompt_roles(self, expected_agents: set[str]) -> None:
        path = self.agents_dir / "local-alignment-roles.json"
        if not path.exists() and not path.is_symlink():
            self.unknown("agent_prompt", "role-map", "prompt_role_map_missing")
            return
        value = self.read_json(
            path,
            "agent_prompt",
            "role-map",
            require_private=True,
        )
        roles = value.get("roles") if value else None
        if (
            not value
            or set(value) != {"version", "roles"}
            or value.get("version") != 1
            or not isinstance(roles, dict)
            or not roles
            or set(roles) != expected_agents
            or any(
                not isinstance(name, str)
                or AGENT_UNIT.fullmatch(f"buzz-local-{name}.service") is None
                or role not in KNOWN_PROMPT_ROLES - {"common"}
                for name, role in roles.items()
            )
        ):
            self.fail("agent_prompt", "role-map", "prompt_role_map_invalid")
            return
        self.prompt_roles = dict(roles)
        self.pass_("agent_prompt", "role-map")

    def infer_prompt_role(self, agent: str) -> str | None:
        configured = self.prompt_roles.get(agent)
        if configured:
            return configured
        for suffix, role in (
            ("-investigator", "investigator"),
            ("-executor", "executor"),
            ("-feature", "feature"),
            ("-desk", "desk"),
            ("-dev", "dev"),
            ("-bi", "bi"),
            ("-bug", "bug"),
            ("-debt", "debt"),
            ("-sre", "sre"),
            ("-qa", "qa"),
        ):
            if agent.endswith(suffix):
                return role
        return None

    def audit_prompt(self, agent: str, path: Path) -> None:
        subject = f"agent:{agent}"
        text = self.read_text(
            path,
            "agent_prompt",
            subject,
            require_private=True,
        )
        if text is None:
            return
        release = str(self.release_root / self.expected_sha)
        render = lambda marker: marker.replace("<RELEASE>", release)
        common = self.prompt_contract["common"]
        missing = [render(marker) for marker in common["required"] if render(marker) not in text]
        role = self.infer_prompt_role(agent)
        if role is None:
            self.unknown("agent_prompt", subject, "prompt_role_unresolved")
        role_contract = self.prompt_contract.get(role or "", {})
        missing.extend(
            render(marker)
            for marker in role_contract.get("required", [])
            if render(marker) not in text
        )
        forbidden = [
            marker
            for marker in [
                *common["forbidden"],
                *role_contract.get("forbidden", []),
            ]
            if marker in text
        ]
        if forbidden:
            self.fail(
                "agent_prompt",
                subject,
                "retired_markers_present",
                f"count={len(forbidden)}",
            )
        refs = set(RELEASE_REF.findall(text))
        if refs != {self.expected_sha}:
            self.fail("agent_prompt", subject, "helper_revision_mismatch")
        if missing:
            self.fail("agent_prompt", subject, "required_markers_missing", f"count={len(set(missing))}")
        if not missing and not forbidden and refs == {self.expected_sha}:
            self.pass_("agent_prompt", subject)

    def audit_responsible_config(
        self, agent: str, path: Path
    ) -> tuple[Path | None, Path | None]:
        subject = f"agent:{agent}"
        value = self.read_json(
            path,
            "responsible_config",
            subject,
            require_private=True,
        )
        if value is None:
            return None, None
        valid = True
        if (
            set(value) != RESPONSIBLE_KEYS
            or value.get("version") != 2
            or not self.validate_responsible_value(value)
        ):
            self.fail("responsible_config", subject, "contract_not_v2")
            valid = False
        if not isinstance(value.get("channels"), list) or not value.get("channels"):
            self.fail("responsible_config", subject, "channels_not_nonempty_list")
            valid = False
        raw_people = value.get("people_file")
        people = Path(raw_people) if isinstance(raw_people, str) and Path(raw_people).is_absolute() else None
        if people is None:
            self.fail("responsible_config", subject, "people_file_path_invalid")
            valid = False
        else:
            people_value = self.read_json(
                people,
                "responsible_config",
                subject,
                require_private=True,
            )
            if not people_value:
                self.fail("responsible_config", subject, "people_file_empty_or_invalid")
                valid = False
        raw_state = value.get("state_dir")
        state_dir = (
            Path(raw_state)
            if isinstance(raw_state, str) and Path(raw_state).is_absolute()
            else None
        )
        if state_dir is None:
            self.fail("responsible_config", subject, "state_dir_path_invalid")
            valid = False
        if not self.audit_buzz_cli(agent, value.get("buzz")):
            valid = False
        if valid:
            self.pass_("responsible_config", subject)
        return people, state_dir

    def audit_buzz_cli(self, agent: str, raw: Any) -> bool:
        subject = f"agent:{agent}"
        if not isinstance(raw, dict) or set(raw) != BUZZ_KEYS:
            self.fail("buzz_cli", subject, "config_invalid")
            return False
        raw_path = raw.get("cli_path")
        expected = raw.get("cli_sha256")
        if (
            not isinstance(raw_path, str)
            or not Path(raw_path).is_absolute()
            or not isinstance(expected, str)
            or HEX64.fullmatch(expected) is None
        ):
            self.fail("buzz_cli", subject, "pin_invalid")
            return False
        path = Path(raw_path)
        self.cli_paths.add(path)
        descriptor: int | None = None
        try:
            if path.resolve(strict=True) != path:
                raise OSError("non-canonical path")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BUZZ_BINARY_BYTES:
                raise OSError("unsafe or oversized binary")
            magic = os.read(descriptor, 4)
            digest = hashlib.sha256(magic)
            total = len(magic)
            while total <= MAX_BUZZ_BINARY_BYTES:
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, MAX_BUZZ_BINARY_BYTES + 1 - total),
                )
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
            after = os.fstat(descriptor)
            stable = (
                before.st_dev == after.st_dev
                and before.st_ino == after.st_ino
                and before.st_size == after.st_size == total
                and before.st_mtime_ns == after.st_mtime_ns
            )
            if total > MAX_BUZZ_BINARY_BYTES or not stable:
                raise OSError("binary changed or exceeded limit")
        except OSError:
            self.fail("buzz_cli", subject, "binary_unreadable")
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)
        good = (
            before.st_uid in {0, os.geteuid()}
            and os.access(path, os.X_OK)
            and not bool(stat.S_IMODE(before.st_mode) & 0o022)
            and path.name == "buzz"
            and len(path.parents) >= 3
            and path.parents[2].name == BUZZ_CLI_RELEASE_DIR
            and magic == b"\x7fELF"
            and digest.hexdigest() == expected
        )
        if not good:
            self.fail("buzz_cli", subject, "binary_or_digest_mismatch")
            return False
        self.pass_("buzz_cli", subject)
        return True

    @staticmethod
    def normalized_allow_read(
        raw: Any, home: Path, cwd: Path
    ) -> set[Path] | None:
        if not isinstance(raw, list):
            return None
        paths: set[Path] = set()
        for value in raw:
            if not isinstance(value, str):
                return None
            if value == ".":
                path = cwd
            else:
                if value.startswith("~/"):
                    value = str(home / value[2:])
                path = Path(value)
            if not path.is_absolute() or ".." in path.parts:
                return None
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                return None
            if resolved != path or resolved in paths:
                return None
            paths.add(resolved)
        if len(paths) != len(raw):
            return None
        return paths

    def unsafe_sandbox_paths(self, paths: set[Path]) -> bool:
        broad = {Path("/"), self.home, *self.home.parents}
        return bool(paths & broad)

    def audit_sandbox(
        self,
        agent: str,
        responsible: Path,
        people: Path | None,
        state_dir: Path | None,
        launch: tuple[Path, dict[str, str]] | None,
    ) -> None:
        subject = f"agent:{agent}"
        if launch is None:
            self.unknown("sandbox", subject, "launcher_unresolved")
            return
        runtime_env = launch[1]
        command = runtime_env.get("BUZZ_ACP_MEDIA_ADAPTER_COMMAND") or runtime_env.get(
            "BUZZ_ACP_AGENT_COMMAND", ""
        )
        if Path(command).name != "claude-agent-acp":
            self.add("sandbox", subject, "not_applicable", "non_claude_harness")
            return
        workdir = launch[0]
        candidates = [workdir / ".claude/settings.local.json", workdir / ".claude/settings.json"]
        present = [path for path in candidates if path.exists() or path.is_symlink()]
        if len(present) != 1:
            self.fail("sandbox", subject, "settings_sources_ambiguous")
            return
        settings_path = present[0]
        if not settings_path.is_file() or settings_path.is_symlink():
            self.fail("sandbox", subject, "agent_settings_missing")
            return
        value = self.read_json(
            settings_path,
            "sandbox",
            subject,
            require_trusted=True,
        )
        if value is None:
            return
        sandbox = value.get("sandbox")
        if not isinstance(sandbox, dict):
            self.fail("sandbox", subject, "sandbox_object_missing")
            return
        filesystem = sandbox.get("filesystem")
        allow_read = self.normalized_allow_read(
            filesystem.get("allowRead") if isinstance(filesystem, dict) else None,
            self.home,
            workdir,
        )
        allow_write = self.normalized_allow_read(
            filesystem.get("allowWrite") if isinstance(filesystem, dict) else None,
            self.home,
            workdir,
        )
        deny_read = (
            filesystem.get("denyRead") if isinstance(filesystem, dict) else None
        )
        permissions = value.get("permissions")
        deny_rules = (
            permissions.get("deny") if isinstance(permissions, dict) else None
        )
        wrapper = self.resolve_path(runtime_env.get("HARNESS_CLAUDE_WRAPPER"))
        claude_home = ".claude-glm" if wrapper and wrapper.name == "claude-glm" else ".claude-buzz"
        required_denies = {
            "Read(~/.config/buzz/**)",
            "Read(~/.bash_secrets)",
            "Read(~/.claude/**)",
            f"Read(~/{claude_home}/.credentials.json)",
            f"Read(~/{claude_home}/projects/**)",
            "Read(~/.config/glab-cli/**)",
        }
        required = {self.release_root / self.expected_sha, responsible}
        if people is not None:
            required.add(people)
        good = (
            set(sandbox) == {
                "enabled",
                "failIfUnavailable",
                "allowUnsandboxedCommands",
                "filesystem",
            }
            and isinstance(filesystem, dict)
            and set(filesystem) == {"denyRead", "allowRead", "allowWrite"}
            and sandbox.get("enabled") is True
            and sandbox.get("failIfUnavailable") is True
            and sandbox.get("allowUnsandboxedCommands") is False
            and isinstance(deny_read, list)
            and "~/" in deny_read
            and allow_read == required | {workdir}
            and state_dir is not None
            and allow_write == {state_dir}
            and self.trusted_private_directory(state_dir)
            and not self.unsafe_sandbox_paths(allow_read)
            and not self.unsafe_sandbox_paths(allow_write)
            and isinstance(permissions, dict)
            and set(permissions) == {"deny"}
            and isinstance(deny_rules, list)
            and all(isinstance(item, str) for item in deny_rules)
            and required_denies <= set(deny_rules)
        )
        if not good:
            self.fail("sandbox", subject, "sandbox_baseline_mismatch")
        else:
            self.pass_("sandbox", subject)

    def register_harness(
        self,
        agent: str,
        _env: dict[str, str],
        launch: tuple[Path, dict[str, str]] | None,
    ) -> None:
        if launch is None:
            self.harnesses.add(("unknown", f"agent:{agent}:launcher-unresolved"))
            return
        workdir, runtime_env = launch
        command = runtime_env.get("BUZZ_ACP_MEDIA_ADAPTER_COMMAND") or runtime_env.get(
            "BUZZ_ACP_AGENT_COMMAND", ""
        )
        adapter_name = Path(command).name if Path(command).is_absolute() else ""
        safe_path = runtime_env.get("PATH")
        if adapter_name == "claude-agent-acp":
            wrapper = runtime_env.get("HARNESS_CLAUDE_WRAPPER")
            raw_config = runtime_env.get("CLAUDE_CONFIG_DIR")
            name = Path(wrapper).name if wrapper else ""
            config = Path(raw_config) if raw_config else None
            expected_config = self.home / (
                ".claude-glm" if name == "claude-glm" else ".claude-buzz"
            )
            if (
                name in {"claude-buzz", "claude-glm"}
                and config == expected_config
                and self.trusted_private_directory(expected_config)
                and safe_path is not None
                and self.trusted_path_list(safe_path)
            ):
                self.harnesses.add(
                    (
                        "claude",
                        json.dumps(
                            [agent, wrapper, str(config), str(workdir), safe_path],
                            separators=(",", ":"),
                        ),
                    )
                )
            else:
                self.harnesses.add(("unknown", f"agent:{agent}"))
        elif adapter_name == "codex-acp":
            raw_codex_home = runtime_env.get("CODEX_HOME")
            codex_home = Path(raw_codex_home) if raw_codex_home else None
            raw_codex_path = runtime_env.get("CODEX_PATH")
            codex_path = Path(raw_codex_path) if raw_codex_path else None
            resolved_codex = (
                self.resolved_trusted_executable(codex_path) if codex_path else None
            )
            try:
                codex_home_good = (
                    codex_home is not None
                    and codex_home.is_absolute()
                    and codex_home.resolve(strict=True) == codex_home
                )
            except OSError:
                codex_home_good = False
            if (
                codex_home_good
                and resolved_codex is not None
                and codex_path == resolved_codex
                and safe_path is not None
                and self.trusted_path_list(safe_path)
            ):
                self.harnesses.add(
                    (
                        "codex",
                        json.dumps(
                            [agent, str(codex_home), str(resolved_codex), str(workdir), safe_path],
                            separators=(",", ":"),
                        ),
                    )
                )
            else:
                self.harnesses.add(("unknown", f"agent:{agent}:codex-runtime-unresolved"))
        elif adapter_name in {"grok", "grok-acp"}:
            self.harnesses.add(
                (
                    "grok",
                    json.dumps(
                        [agent, str(self.home / ".grok")],
                        separators=(",", ":"),
                    ),
                )
            )
        else:
            self.harnesses.add(("unknown", f"agent:{agent}"))

    @staticmethod
    def resolved_trusted_executable(path: Path) -> Path | None:
        try:
            resolved = path.resolve(strict=True)
            metadata = resolved.lstat()
        except OSError:
            return None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in {0, os.geteuid()}
            or not bool(stat.S_IMODE(metadata.st_mode) & 0o111)
            or bool(stat.S_IMODE(metadata.st_mode) & 0o022)
            or not trusted_directory_chain(resolved.parent, os.geteuid())
        ):
            return None
        return resolved

    @staticmethod
    def resolved_trusted_directory(path: Path, *, canonical: bool = False) -> Path | None:
        try:
            resolved = path.resolve(strict=True)
            metadata = resolved.lstat()
        except OSError:
            return None
        if (
            not path.is_absolute()
            or ".." in path.parts
            or (canonical and resolved != path)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, os.geteuid()}
            or bool(stat.S_IMODE(metadata.st_mode) & 0o022)
            or not trusted_directory_chain(resolved.parent, os.geteuid())
        ):
            return None
        return resolved

    def audit_media_proxy(self, agent: str, env: dict[str, str]) -> None:
        subject = f"agent:{agent}"
        proxy = self.resolve_path(env.get("BUZZ_ACP_AGENT_COMMAND"))
        adapter = self.resolve_path(env.get("BUZZ_ACP_MEDIA_ADAPTER_COMMAND"))
        cli = self.resolve_path(env.get("BUZZ_ACP_MEDIA_BUZZ_CLI"))
        if adapter is None and cli is None:
            if (
                self.assignment_value(env.get("BUZZ_ACP_MEDIA_MODE"))
                == "stock_text_only"
                and proxy is not None
                and self.resolved_trusted_executable(proxy) is not None
            ):
                self.add("media_proxy", subject, "not_applicable", "stock_text_only")
            else:
                self.fail("media_proxy", subject, "media_proxy_not_configured")
            return
        if (
            proxy is None
            or adapter is None
            or cli is None
            or "BUZZ_ACP_MEDIA_MODE" in env
        ):
            self.fail("media_proxy", subject, "media_proxy_not_configured")
            return
        release = self.release_root / self.expected_sha
        if not self.validate_release(
            release,
            "media_proxy",
            subject,
            "buzz_acp_media_proxy.py",
        ):
            return
        expected_source = release / "scripts/buzz_acp_media_proxy.py"
        source_text = self.read_text(
            expected_source,
            "media_proxy",
            subject,
            require_trusted=True,
        )
        proxy_text = self.read_text(
            proxy,
            "media_proxy",
            subject,
            require_trusted=True,
        )
        try:
            directory_meta = proxy.parent.lstat()
            proxy_meta = proxy.lstat()
        except OSError:
            directory_meta = None
            proxy_meta = None
        expected_digest = (
            hashlib.sha256(source_text.encode("utf-8")).hexdigest()
            if source_text is not None
            else None
        )
        actual_digest = (
            hashlib.sha256(proxy_text.encode("utf-8")).hexdigest()
            if proxy_text is not None
            else None
        )
        good = (
            expected_digest is not None
            and actual_digest == expected_digest == proxy.parent.name
            and proxy.parent.parent
            == self.home / ".local/share/buzz-agent-setup/acp-media-proxy"
            and proxy.name == adapter.name
            and directory_meta is not None
            and stat.S_ISDIR(directory_meta.st_mode)
            and directory_meta.st_uid == os.geteuid()
            and stat.S_IMODE(directory_meta.st_mode) == 0o700
            and proxy_meta is not None
            and stat.S_IMODE(proxy_meta.st_mode) == 0o555
            and self.resolved_trusted_executable(adapter) is not None
            and self.resolved_trusted_executable(cli) is not None
            and cli in self.cli_paths
        )
        if adapter.name == "claude-agent-acp":
            claude = self.resolve_path(env.get("CLAUDE_CODE_EXECUTABLE"))
            wrapper = self.resolve_path(env.get("HARNESS_CLAUDE_WRAPPER"))
            good = (
                good
                and wrapper is not None
                and self.resolved_trusted_executable(wrapper) is not None
                and (
                    claude is None
                    or (
                        self.resolved_trusted_executable(claude) is not None
                        and claude.resolve(strict=True) == wrapper.resolve(strict=True)
                    )
                )
            )
        if good:
            self.pass_("media_proxy", subject)
        else:
            self.fail("media_proxy", subject, "media_proxy_contract_mismatch")

    def audit_launcher_preflight(
        self, agent: str, raw_env: dict[str, str]
    ) -> tuple[Path, dict[str, str]] | None:
        subject = f"agent:{agent}"
        try:
            environment = {
                key: value
                for key, raw in raw_env.items()
                if (value := self.assignment_value(raw)) is not None
            }
            if len(environment) != len(raw_env):
                raise ValueError("env contains a non-literal value")
            home = self.resolved_trusted_directory(self.home, canonical=True)
            work_root = self.resolved_trusted_directory(self.home / "buzz-agent-work")
            if home is None or work_root is None:
                raise ValueError("owner paths are untrusted")
            workdir = self.resolved_trusted_directory(
                Path(environment.get("AGENT_WORKDIR", str(work_root / agent)))
            )
            if workdir is None or not workdir.is_relative_to(work_root):
                raise ValueError("workdir is outside the owner work root")
            legacy = workdir / "naturehood"
            if "AGENT_WORKDIR" not in environment and (legacy / ".git").exists():
                workdir = self.resolved_trusted_directory(legacy)
            if workdir is None or not (workdir / ".git").exists():
                raise ValueError("workdir is not a Git checkout")

            raw_path = environment.get(
                "BUZZ_AGENT_SAFE_PATH",
                f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin",
            )
            safe_parts: list[str] = []
            for raw in raw_path.split(":"):
                resolved = self.resolved_trusted_directory(Path(raw)) if raw else None
                if resolved is None:
                    raise ValueError("PATH contains an unsafe directory")
                if str(resolved) not in safe_parts:
                    safe_parts.append(str(resolved))
            safe_path = ":".join(safe_parts)

            proxy = self.resolve_path(raw_env.get("BUZZ_ACP_AGENT_COMMAND"))
            resolved_proxy = self.resolved_trusted_executable(proxy) if proxy else None
            raw_adapter = self.resolve_path(raw_env.get("BUZZ_ACP_MEDIA_ADAPTER_COMMAND"))
            raw_cli = self.resolve_path(raw_env.get("BUZZ_ACP_MEDIA_BUZZ_CLI"))
            media_mode = environment.get("BUZZ_ACP_MEDIA_MODE")
            if bool(raw_adapter) != bool(raw_cli):
                raise ValueError("media adapter and CLI disagree")
            if raw_adapter is None and media_mode != "stock_text_only":
                raise ValueError("stock text mode is not explicit")
            if raw_adapter is not None and media_mode is not None:
                raise ValueError("media mode conflicts with proxy mode")
            adapter = (
                self.resolved_trusted_executable(raw_adapter)
                if raw_adapter is not None
                else resolved_proxy
            )
            media_cli = (
                self.resolved_trusted_executable(raw_cli) if raw_cli is not None else None
            )
            if resolved_proxy is None or adapter is None or (raw_cli and media_cli is None):
                raise ValueError("ACP command paths are untrusted")
            environment["BUZZ_ACP_AGENT_COMMAND"] = str(resolved_proxy)
            if media_cli is not None:
                environment["BUZZ_ACP_MEDIA_ADAPTER_COMMAND"] = str(adapter)
                environment["BUZZ_ACP_MEDIA_BUZZ_CLI"] = str(media_cli)

            if adapter.name == "claude-agent-acp":
                wrapper_raw = self.resolve_path(raw_env.get("HARNESS_CLAUDE_WRAPPER"))
                wrapper = (
                    self.resolved_trusted_executable(wrapper_raw) if wrapper_raw else None
                )
                config = self.resolve_path(raw_env.get("CLAUDE_CONFIG_DIR"))
                expected_config = self.home / (
                    ".claude-glm"
                    if wrapper is not None and wrapper.name == "claude-glm"
                    else ".claude-buzz"
                )
                configured = self.resolve_path(raw_env.get("CLAUDE_CODE_EXECUTABLE"))
                resolved_configured = (
                    self.resolved_trusted_executable(configured) if configured else None
                )
                if (
                    wrapper is None
                    or wrapper.name not in {"claude-buzz", "claude-glm"}
                    or config != expected_config
                    or not self.trusted_private_directory(expected_config)
                    or (configured is not None and resolved_configured != wrapper)
                ):
                    raise ValueError("Claude launcher contract is invalid")
                environment["HARNESS_CLAUDE_WRAPPER"] = str(wrapper)
                environment["CLAUDE_CODE_EXECUTABLE"] = str(wrapper)
                environment["CLAUDE_CONFIG_DIR"] = str(config)
            elif adapter.name == "codex-acp":
                codex_home_raw = self.resolve_path(raw_env.get("CODEX_HOME"))
                codex_home = (
                    self.resolved_trusted_directory(codex_home_raw)
                    if codex_home_raw
                    else None
                )
                codex_path_raw = self.resolve_path(raw_env.get("CODEX_PATH"))
                codex_path = (
                    self.resolved_trusted_executable(codex_path_raw)
                    if codex_path_raw
                    else None
                )
                if (
                    codex_home is None
                    or codex_path is None
                    or codex_path_raw != codex_path
                ):
                    raise ValueError("Codex launcher contract is invalid")
                environment["CODEX_HOME"] = str(codex_home)
                environment["CODEX_PATH"] = str(codex_path)

            binary_raw = self.resolve_path(raw_env.get("BUZZ_ACP_BINARY"))
            binary = (
                self.resolved_trusted_executable(binary_raw) if binary_raw else None
            )
            digest = environment.get("BUZZ_ACP_BINARY_SHA256", "")
            if binary is None or binary_raw != binary or HEX64.fullmatch(digest) is None:
                raise ValueError("buzz-acp binary pin is invalid")
            mode, contents = read_immutable_file(
                binary,
                MAX_BUZZ_BINARY_BYTES,
                allow_owner_write=True,
            )
            if (
                not mode & 0o111
                or contents[:4] != b"\x7fELF"
                or hashlib.sha256(contents).hexdigest() != digest
            ):
                raise ValueError("buzz-acp binary does not match its pin")
            environment.update(
                {
                    "HOME": str(home),
                    "USER": "audit-owner",
                    "LOGNAME": "audit-owner",
                    "SHELL": "/bin/bash",
                    "PATH": safe_path,
                    "LANG": environment.get("LANG", "C.UTF-8"),
                    "TERM": "dumb",
                    "PWD": str(workdir),
                }
            )
        except (OSError, TypeError, ValueError):
            self.fail("agent_env", subject, "launcher_preflight_failed")
            return None
        self.pass_("agent_env", subject, "launcher_preflight_ok")
        return workdir, environment

    def audit_agent(self, unit: Path, agent: str) -> None:
        subject = f"agent:{agent}"
        unit_ok = self.regular_file(
            unit, "agent_unit", subject, "unit_not_regular_or_symlink"
        )
        dropins_ok = self.reject_unit_dropins(unit, "agent_unit", subject)
        if unit_ok and dropins_ok:
            unit_text = self.read_text(
                unit,
                "agent_unit",
                subject,
                require_trusted=True,
            )
            log_path = str(self.agents_dir / f"{agent}.log")
            expected = {
                "Type": "exec",
                "UMask": "0077",
                "NoNewPrivileges": "yes",
                "UnsetEnvironment": SYSTEMD_UNSET_ENVIRONMENT,
                "Restart": "on-failure",
                "RestartSec": "5s",
                "StandardOutput": f"append:{log_path}",
                "StandardError": f"append:{log_path}",
            }
            template_good = unit_text is not None and all(
                [value.replace("%h", str(self.home))]
                == [item.replace("%h", str(self.home)) for item in self.unit_values(unit_text, "Service", key)]
                for key, value in expected.items()
            )
            template_good = bool(
                template_good
                and unit_text is not None
                and set(self.unit_keys(unit_text, "Service"))
                == {*expected, "ExecStart"}
                and self.unit_sections(unit_text) <= {"Unit", "Service", "Install"}
                and set(self.unit_keys(unit_text, "Unit")) <= {"Description"}
                and set(self.unit_keys(unit_text, "Install")) == {"WantedBy"}
                and not self.unit_values(unit_text, "Service", "Environment")
                and not self.unit_values(unit_text, "Service", "EnvironmentFile")
                and self.unit_values(unit_text, "Install", "WantedBy") == ["default.target"]
            )
            if not template_good:
                self.fail("agent_unit", subject, "service_template_mismatch")
            exec_start = self.unit_exec(
                unit, "agent_unit", subject, forbid_inline_environment=True
            )
            try:
                argv = shlex.split(exec_start or "", posix=True)
            except ValueError:
                argv = []
            expected_run = self.agents_dir / "run-agent.py"
            if argv != ["/usr/bin/python3", "-I", str(expected_run), agent]:
                self.fail("agent_unit", subject, "launcher_mismatch")
            else:
                self.pass_("agent_unit", subject)
        env = self.parse_env(self.agents_dir / f"{agent}.env", subject)
        if env is None:
            return
        launch = self.audit_launcher_preflight(agent, env)
        prompt = self.resolve_path(env.get("BUZZ_ACP_SYSTEM_PROMPT_FILE"))
        if prompt is None:
            self.fail("agent_prompt", subject, "prompt_path_invalid")
        else:
            self.audit_prompt(agent, prompt)
        responsible = self.resolve_path(env.get("BUZZ_RESPONSIBLE_CONFIG"))
        if responsible is None:
            self.fail("responsible_config", subject, "config_path_invalid")
        else:
            people, state_dir = self.audit_responsible_config(agent, responsible)
            self.audit_sandbox(agent, responsible, people, state_dir, launch)
        self.audit_media_proxy(agent, env)
        self.register_harness(agent, env, launch)

    def audit_timer_pair(
        self,
        service: Path,
        category: str,
        subject: str,
        *,
        interval: str,
        require_persistent: bool,
        accuracy: str | None = None,
    ) -> str | None:
        timer = service.with_suffix(".timer")
        if not self.regular_file(
            timer, category, subject, "timer_missing_not_regular_or_symlink"
        ):
            return None
        if not self.reject_unit_dropins(timer, category, subject):
            return None
        text = self.read_text(timer, category, subject, require_trusted=True)
        if text is None:
            return None
        template_good = True
        if self.unit_values(text, "Timer", "OnUnitActiveSec") != [interval]:
            self.fail(category, subject, "timer_interval_mismatch")
            template_good = False
        if self.unit_values(text, "Timer", "OnActiveSec") != ["1min"]:
            template_good = False
        if self.unit_values(text, "Timer", "OnBootSec") != ["2min"]:
            template_good = False
        if self.unit_values(text, "Timer", "Unit") != [service.name]:
            self.fail(category, subject, "timer_unit_mismatch")
            template_good = False
        persistent = self.unit_values(text, "Timer", "Persistent")
        if require_persistent and persistent != ["true"]:
            self.fail(category, subject, "timer_not_persistent")
            template_good = False
        if not require_persistent and persistent:
            template_good = False
        if self.unit_values(text, "Timer", "AccuracySec") != ([accuracy] if accuracy else []):
            template_good = False
        if self.unit_values(text, "Install", "WantedBy") != ["timers.target"]:
            template_good = False
        expected_timer_keys = {
            "OnActiveSec",
            "OnBootSec",
            "OnUnitActiveSec",
            "Unit",
            *({"Persistent"} if require_persistent else set()),
            *({"AccuracySec"} if accuracy else set()),
        }
        template_good = bool(
            template_good
            and self.unit_sections(text) <= {"Unit", "Timer", "Install"}
            and set(self.unit_keys(text, "Unit")) <= {"Description"}
            and set(self.unit_keys(text, "Timer")) == expected_timer_keys
            and set(self.unit_keys(text, "Install")) == {"WantedBy"}
        )
        if not template_good:
            self.fail(category, subject, "timer_template_mismatch")
        return text

    def audit_service_baseline(
        self,
        service: Path,
        category: str,
        subject: str,
        timeout: str,
        *,
        network_after: bool,
        extra: dict[str, str] | None = None,
    ) -> bool:
        text = self.read_text(service, category, subject, require_trusted=True)
        if text is None:
            return False
        expected = {
            "Type": "oneshot",
            "UMask": "0077",
            "NoNewPrivileges": "yes",
            "TimeoutStartSec": timeout,
            **(extra or {}),
        }
        good = all(
            self.unit_values(text, "Service", key) == [value]
            for key, value in expected.items()
        )
        good = bool(
            good
            and self.unit_sections(text) <= {"Unit", "Service"}
            and set(self.unit_keys(text, "Unit"))
            <= ({"Description", "After"} if network_after else {"Description"})
            and set(self.unit_keys(text, "Service"))
            == {*expected, "Environment", "ExecStart"}
        )
        good = good and self.unit_values(text, "Unit", "After") == (
            ["network-online.target"] if network_after else []
        )
        if not good:
            self.fail(category, subject, "service_baseline_mismatch")
        return good

    def audit_sync(self, service: Path, name: str) -> None:
        subject = f"sync:{name}"
        if not self.regular_file(
            service, "sync_release", subject, "unit_not_regular_or_symlink"
        ):
            return
        if not self.reject_unit_dropins(service, "sync_release", subject):
            return
        self.audit_service_baseline(
            service,
            "sync_release",
            subject,
            "20min",
            network_after=True,
        )
        self.audit_timer_pair(
            service,
            "sync_release",
            subject,
            interval="300",
            require_persistent=True,
        )
        service_text = self.read_text(
            service,
            "sync_release",
            subject,
            require_trusted=True,
        )
        if service_text is None:
            return
        exec_start = self.unit_exec(service, "sync_release", subject)
        try:
            argv = shlex.split(exec_start or "", posix=True)
        except ValueError:
            argv = []
        release = self.release_root / self.expected_sha
        entrypoint = release / "scripts/gitlab_buzz_sync_timer.py"
        shared_launcher = self.home / ".config/buzz/sync/gitlab-buzz-sync-launch.sh"
        if (
            argv
            != [
                str(shared_launcher),
                "/usr/bin/python3",
                str(entrypoint),
            ]
        ):
            self.fail("sync_release", subject, "launcher_contract_mismatch")
            return
        raw_env_path = self.unit_environment(service_text, "BUZZ_SYNC_ENV_FILE")
        if raw_env_path is None:
            self.fail("sync_release", subject, "service_environment_contract_mismatch")
            return
        env_path = Path(raw_env_path)
        if (
            env_path.parent != self.agents_dir
            or env_path.suffix != ".env"
            or not env_path.is_absolute()
        ):
            self.fail("sync_release", subject, "desk_env_path_invalid")
            return
        agent = env_path.stem
        env = self.parse_env(env_path, f"agent:{agent}")
        if env is None:
            self.fail("sync_release", subject, "desk_env_invalid")
            return
        manifest = self.resolve_path(env.get("BUZZ_DESK_RUNNER_MANIFEST"))
        if manifest is None:
            self.fail("sync_release", subject, "manifest_invalid")
            return
        value = self.read_json(
            manifest,
            "sync_release",
            subject,
            require_private=True,
        )
        raw_release = value.get("release_dir") if value else None
        manifest_release = (
            Path(raw_release)
            if isinstance(raw_release, str) and Path(raw_release).is_absolute()
            else None
        )
        if manifest_release != release:
            self.fail("sync_release", subject, "manifest_release_invalid")
            return
        if not self.validate_release(
            release,
            "sync_release",
            subject,
            "gitlab_buzz_sync_timer.py",
        ):
            return
        # Use the validator shipped beside this already-running auditor.  Never
        # import or execute code from the directory being audited.
        code = (
            "import sys; from pathlib import Path; "
            "sys.path.insert(0, sys.argv[1]); "
            "import gitlab_buzz_desk_runner as r; "
            "m=r.load_manifest(Path(sys.argv[2])); r.inventory(m)"
        )
        try:
            done = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    code,
                    str(Path(__file__).resolve().parent),
                    str(manifest),
                ],
                env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.fail("sync_release", subject, "manifest_validation_unavailable")
            return
        if done.returncode != 0:
            self.fail("sync_release", subject, "manifest_or_sync_config_invalid")

    def audit_direct_release_unit(
        self, service: Path, name: str, category: str, script: str
    ) -> None:
        subject = f"{name}"
        if not self.regular_file(
            service, category, subject, "unit_not_regular_or_symlink"
        ):
            return
        if not self.reject_unit_dropins(service, category, subject):
            return
        contracts = {
            "feishu_release": ("1min", False, "10min", "PATH", True),
            "todo_release": ("600", True, "5min", "BUZZ_TODO_ENV_FILE", True),
            "join_release": ("120", True, "10min", "BUZZ_JOIN_CONFIG", False),
        }
        interval, persistent, timeout, environment_key, network_after = contracts[category]
        extra = {"SuccessExitStatus": "3", "Nice": "5"} if category == "feishu_release" else None
        self.audit_service_baseline(
            service,
            category,
            subject,
            timeout,
            network_after=network_after,
            extra=extra,
        )
        self.audit_timer_pair(
            service,
            category,
            subject,
            interval=interval,
            require_persistent=persistent,
            accuracy="15s" if category == "feishu_release" else None,
        )
        service_text = self.read_text(
            service,
            category,
            subject,
            require_trusted=True,
        )
        if service_text is None:
            return
        environment_value = self.unit_environment(service_text, environment_key)
        if environment_value is None:
            self.fail(category, subject, "service_environment_contract_mismatch")
            return
        if category == "feishu_release" and not self.trusted_path_list(environment_value):
            self.fail(category, subject, "service_environment_contract_mismatch")
            return
        if category in {"todo_release", "join_release"}:
            config_path = Path(environment_value)
            expected_parent = self.home / ".config/buzz" / (
                "todo" if category == "todo_release" else "join"
            )
            if (
                not config_path.is_absolute()
                or ".." in config_path.parts
                or config_path.parent != expected_parent
                or not self.private_file(config_path, category, subject)
            ):
                self.fail(category, subject, "service_environment_contract_mismatch")
                return
            if category == "join_release":
                self.audit_join_config(config_path, subject)
        exec_start = self.unit_exec(service, category, subject)
        try:
            argv = shlex.split(exec_start or "", posix=True)
        except ValueError:
            argv = []
        release = self.release_root / self.expected_sha
        entrypoint = release / "scripts" / script
        good = False
        if category == "feishu_release" and len(argv) == 7:
            config = Path(argv[4])
            state_dir = Path(argv[6])
            good = (
                argv[:4] == ["/usr/bin/python3", str(entrypoint), "round", "--config"]
                and argv[5] == "--state-dir"
                and config.is_absolute()
                and state_dir.is_absolute()
                and ".." not in config.parts
                and ".." not in state_dir.parts
            )
        elif category == "todo_release":
            good = argv == [
                str(self.home / ".config/buzz/todo/gitlab-todo-sync-launch.sh"),
                "/usr/bin/python3",
                str(entrypoint),
            ]
        elif category == "join_release":
            good = argv == ["/usr/bin/python3", str(entrypoint)]
        if not good:
            self.fail(category, subject, "entrypoint_contract_mismatch")
            return
        self.validate_release(release, category, subject, script)

    def audit_join_config(self, path: Path, subject: str) -> None:
        value = self.read_json(path, "join_release", subject, require_private=True)
        if not isinstance(value, dict):
            self.fail("join_release", subject, "config_invalid")
            return
        rows = value.get("agents")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            self.fail("join_release", subject, "config_invalid")
            return
        names = [row.get("name") for row in rows]
        expected = sorted(
            agent for agent, role in self.prompt_roles.items()
            if role in JOIN_MANAGED_PROMPT_ROLES
        )
        if (any(not isinstance(name, str) for name in names)
                or len(set(names)) != len(names) or sorted(names) != expected):
            self.fail(
                "join_release", subject, "agent_coverage_mismatch",
                f"expected={len(expected)},configured={len(names)}",
            )
            return
        bindings_good = all(
            row.get("env_file") == str(self.agents_dir / f"{row['name']}.env")
            and row.get("unit") == f"buzz-local-{row['name']}.service"
            for row in rows
        )
        if not bindings_good:
            self.fail("join_release", subject, "agent_binding_mismatch")
            return
        self.pass_("join_release", f"{subject}:agent-coverage", "all_applicable_agents_managed")

    def audit_join_timer_runtime(self) -> None:
        """A file on disk is not default-on; the real user's timer must also be enabled and active."""

        if not self._verify_user_manager:
            return
        child_env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        if self.runtime_dir is not None:
            child_env["XDG_RUNTIME_DIR"] = str(self.runtime_dir)
        for verb, expected, code in (
            ("is-enabled", "enabled", "timer_not_enabled"),
            ("is-active", "active", "timer_not_active"),
        ):
            try:
                done, output = run_bounded_text(
                    ["/usr/bin/systemctl", "--user", verb, "buzz-agent-join.timer"],
                    env=child_env,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired):
                self.fail("join_release", "buzz-agent-join", code)
                continue
            if done.returncode != 0 or output.strip() != expected:
                self.fail("join_release", "buzz-agent-join", code)
            else:
                self.pass_("join_release", f"buzz-agent-join:{verb}")

    def audit_claude_plugin(self, raw: str) -> None:
        try:
            agent, raw_wrapper, raw_config, raw_workdir, safe_path = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.unknown("plugin_revision", "harness:claude", "harness_unresolved")
            return
        wrapper = Path(raw_wrapper)
        root = Path(raw_config)
        workdir = Path(raw_workdir)
        subject = f"harness:{wrapper.name}:agent:{agent}"
        expected_root = self.home / (
            ".claude-buzz" if wrapper.name == "claude-buzz" else ".claude-glm"
        )
        resolved_wrapper = self.resolved_trusted_executable(wrapper)
        try:
            paths_good = (
                resolved_wrapper is not None
                and resolved_wrapper == wrapper
                and root == expected_root
                and self.trusted_private_directory(root)
                and workdir.resolve(strict=True) == workdir
                and workdir.is_dir()
                and isinstance(safe_path, str)
                and self.trusted_path_list(safe_path)
            )
        except OSError:
            paths_good = False
        if not paths_good:
            self.unknown("plugin_revision", subject, "claude_runtime_unresolved")
            return
        registry = root / "plugins/installed_plugins.json"
        value = self.read_json(
            registry,
            "plugin_revision",
            subject,
            require_trusted=True,
        )
        records = value.get("plugins", {}).get("addx@addx") if value else None
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            self.fail("plugin_revision", subject, "enabled_record_not_unique")
            return
        record = records[0]
        install_raw = record.get("installPath")
        install = Path(install_raw) if isinstance(install_raw, str) and Path(install_raw).is_absolute() else None
        skill = install / "skills/buzz-agent-setup/SKILL.md" if install else None
        try:
            install_good = install is not None and install.resolve(strict=True) == install
        except OSError:
            install_good = False
        settings = self.read_json(
            root / "settings.json",
            "plugin_revision",
            subject,
            require_trusted=True,
        )
        enabled = settings.get("enabledPlugins") if settings else None
        overrides: list[dict[str, Any]] = []
        optional_sources = [
            workdir / ".claude/settings.json",
            workdir / ".claude/settings.local.json",
            root / "remote-settings.json",
            self.managed_settings_root / "managed-settings.json",
        ]
        managed_dir = self.managed_settings_root / "managed-settings.d"
        if managed_dir.exists() or managed_dir.is_symlink():
            try:
                optional_sources.extend(sorted(managed_dir.glob("*.json")))
            except OSError:
                self.fail("plugin_revision", subject, "managed_settings_unreadable")
        for source in optional_sources:
            if not (source.exists() or source.is_symlink()):
                continue
            value = self.read_json(source, "plugin_revision", subject, require_trusted=True)
            if value is None:
                overrides.append({"enabledPlugins": {"addx@addx": False}})
                continue
            overrides.append(value)
        effective_overrides = [
            value["enabledPlugins"]["addx@addx"]
            for value in overrides
            if isinstance(value.get("enabledPlugins"), dict)
            and "addx@addx" in value["enabledPlugins"]
        ]
        if (
            not isinstance(enabled, dict)
            or enabled.get("addx@addx") is not True
            or any(value is not True for value in effective_overrides)
        ):
            self.fail("plugin_revision", subject, "plugin_not_enabled")
        elif record.get("gitCommitSha") != self.expected_sha:
            self.fail("plugin_revision", subject, "revision_mismatch")
        elif (
            not install_good
            or install is None
            or install.is_symlink()
            or skill is None
            or not skill.is_file()
            or skill.is_symlink()
        ):
            self.fail("plugin_revision", subject, "install_or_skill_invalid")
        elif not self.validate_installed_skill_tree(install / "skills/buzz-agent-setup"):
            self.fail("plugin_revision", subject, "installed_skill_tree_mismatch")
        else:
            self.pass_("plugin_revision", subject)
        sandbox = settings.get("sandbox") if settings else None
        credentials = sandbox.get("credentials") if isinstance(sandbox, dict) else None
        entries = credentials.get("envVars") if isinstance(credentials, dict) else None
        if not (
            isinstance(sandbox, dict)
            and set(sandbox) == {"allowUnsandboxedCommands", "credentials"}
            and sandbox.get("allowUnsandboxedCommands") is False
            and isinstance(credentials, dict)
            and set(credentials) == {"envVars"}
            and isinstance(entries, list)
            and entries
            and all(
                isinstance(item, dict)
                and set(item) == {"name", "mode"}
                and isinstance(item.get("name"), str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item["name"])
                and item.get("mode") == "deny"
                for item in entries
            )
        ):
            self.fail("sandbox", subject, "shared_sandbox_policy_missing")
        else:
            self.pass_("sandbox", subject)

    def audit_codex_plugin(self, raw: str) -> None:
        try:
            agent, raw_home, raw_binary, raw_workdir, safe_path = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.unknown("plugin_revision", "harness:codex", "harness_unresolved")
            return
        subject = f"harness:codex:agent:{agent}"
        root = Path(raw_home)
        binary = self.resolved_trusted_executable(Path(raw_binary))
        workdir = Path(raw_workdir)
        try:
            paths_good = (
                root.is_absolute()
                and ".." not in root.parts
                and root.resolve(strict=True) == root
                and binary is not None
                and binary == Path(raw_binary)
                and workdir.resolve(strict=True) == workdir
                and workdir.is_dir()
                and isinstance(safe_path, str)
                and self.trusted_path_list(safe_path)
            )
        except OSError:
            paths_good = False
        if not paths_good:
            self.unknown("plugin_revision", subject, "codex_runtime_unresolved")
            return
        config_text = self.read_text(
            root / "config.toml",
            "plugin_revision",
            subject,
            require_trusted=True,
        )
        config = self.parse_codex_plugin_config(config_text) if config_text is not None else None
        if config is None:
            self.fail("plugin_revision", subject, "codex_config_invalid")
            return
        plugin = config['plugins."addx@addx"']
        marketplace = config["marketplaces.addx"]
        marketplace_source = marketplace.get("source")
        if plugin.get("enabled") is not True:
            self.fail("plugin_revision", subject, "plugin_not_enabled")
            return
        if (
            marketplace.get("source_type") != "git"
            or not isinstance(marketplace_source, str)
            or marketplace_source != CANONICAL_SKILLS_REMOTE
            or marketplace.get("ref") not in (None, "main")
        ):
            self.fail("plugin_revision", subject, "codex_marketplace_invalid")
            return
        install_root = root / "plugins/cache/addx/addx"
        installs: list[Path] = []
        try:
            if install_root.resolve(strict=True) != install_root:
                raise OSError("non-canonical Codex cache root")
            with os.scandir(install_root) as entries:
                for index, entry in enumerate(entries, start=1):
                    if index > MAX_DISCOVERY_ENTRIES:
                        raise OSError("too many Codex cache entries")
                    if (
                        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", entry.name)
                        is None
                        or not entry.is_dir(follow_symlinks=False)
                    ):
                        raise OSError("unexpected Codex cache entry")
                    installs.append(Path(entry.path))
        except OSError:
            self.fail("plugin_revision", subject, "codex_cache_unreadable")
            return
        if len(installs) != 1:
            self.fail("plugin_revision", subject, "enabled_install_not_unique")
            return
        install = installs[0]
        metadata = self.read_json(
            install / ".codex-marketplace-install.json",
            "plugin_revision",
            subject,
            require_trusted=True,
        )
        if (
            metadata is None
            or metadata.get("source_type") != "git"
            or metadata.get("source") != marketplace_source
            or metadata.get("ref_name") not in (None, "main")
            or metadata.get("sparse_paths") != []
            or metadata.get("revision") != self.expected_sha
        ):
            self.fail("plugin_revision", subject, "revision_mismatch")
        elif not self.validate_installed_skill_tree(install / "skills/buzz-agent-setup"):
            self.fail("plugin_revision", subject, "installed_skill_tree_mismatch")
        else:
            self.pass_("plugin_revision", subject)

    def audit_grok_plugin(self, raw: str) -> None:
        try:
            agent, raw_home = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.unknown("plugin_revision", "harness:grok", "harness_unresolved")
            return
        subject = f"harness:grok:agent:{agent}"
        root = Path(raw_home)
        value = self.read_json(
            root / "installed-plugins/registry.json",
            "plugin_revision",
            subject,
            require_trusted=True,
        )
        repos = value.get("repos") if value else None
        matches: list[dict[str, Any]] = []
        if isinstance(repos, dict):
            for repo in repos.values():
                if isinstance(repo, dict) and isinstance(repo.get("kind"), dict):
                    kind = repo["kind"]
                    if kind.get("type") == "Git" and str(kind.get("url", "")).endswith("engineering/skills.git"):
                        matches.append(repo)
        if len(matches) != 1:
            self.fail("plugin_revision", subject, "skills_repo_not_unique")
            return
        repo = matches[0]
        install = Path(str(repo.get("path", "")))
        skill = install / "skills/buzz-agent-setup/SKILL.md"
        try:
            install_good = install.is_absolute() and install.resolve(strict=True) == install
        except OSError:
            install_good = False
        if repo["kind"].get("commit") != self.expected_sha:
            self.fail("plugin_revision", subject, "revision_mismatch")
        elif (
            not install_good
            or ".." in install.parts
            or not skill.is_file()
            or skill.is_symlink()
        ):
            self.fail("plugin_revision", subject, "install_or_skill_invalid")
        elif not self.validate_installed_skill_tree(install / "skills/buzz-agent-setup"):
            self.fail("plugin_revision", subject, "installed_skill_tree_mismatch")
        else:
            self.pass_("plugin_revision", subject)

    def audit_harnesses(self) -> None:
        for kind, value in sorted(self.harnesses):
            if kind == "claude":
                self.audit_claude_plugin(value)
            elif kind == "codex":
                self.audit_codex_plugin(value)
            elif kind == "grok":
                self.audit_grok_plugin(value)
            else:
                self.unknown("plugin_revision", value, "harness_unresolved")

    @staticmethod
    def public_harness_identity(kind: str, value: str) -> str:
        if kind in {"claude", "codex", "grok"}:
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                return f"{kind}:unresolved"
            if (
                isinstance(decoded, list)
                and decoded
                and isinstance(decoded[0], str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", decoded[0])
            ):
                return f"{kind}:agent:{decoded[0]}"
            return f"{kind}:unresolved"
        return f"{kind}:{value}"

    def public_cli_identities(self) -> list[str]:
        return ["buzz-cli"] if self.cli_paths else []

    @staticmethod
    def documented_template(path: Path, marker: str) -> str:
        text = path.read_text(encoding="utf-8")
        if not marker:
            return text if text.endswith("\n") else text + "\n"
        match = re.search(
            rf"<!-- template:{re.escape(marker)} -->\s*```bash\n(.*?)\n```",
            text,
            re.DOTALL,
        )
        if match is None:
            raise ValueError("documented launcher template is missing")
        return match.group(1).strip() + "\n"

    @staticmethod
    def normalize_sync_launcher(text: str) -> str | None:
        lines: list[str] = []
        found = False
        for line in text.splitlines():
            if line.startswith("GITLAB_TOKEN_ENVS="):
                if (
                    found
                    or re.fullmatch(
                        r"GITLAB_TOKEN_ENVS=\([A-Z][A-Z0-9_]*(?: [A-Z][A-Z0-9_]*)*\)",
                        line,
                    )
                    is None
                ):
                    return None
                lines.append("GITLAB_TOKEN_ENVS=(<configured>)")
                found = True
            else:
                lines.append(line)
        return "\n".join(lines).strip() + "\n" if found else None

    def audit_documented_launcher(
        self,
        path: Path,
        subject: str,
        reference: Path,
        marker: str,
        *,
        normalize_sync: bool = False,
        expected_mode: int = 0o700,
    ) -> None:
        if not self.owner_executable(
            path,
            "shared_launcher",
            subject,
            "launcher_not_owner_executable",
        ):
            return
        try:
            if stat.S_IMODE(path.lstat().st_mode) != expected_mode:
                raise OSError("launcher mode mismatch")
            expected = self.documented_template(reference, marker)
        except (OSError, UnicodeDecodeError, ValueError):
            self.fail("shared_launcher", subject, "launcher_template_unavailable")
            return
        actual = self.read_text(
            path,
            "shared_launcher",
            subject,
            require_trusted=True,
        )
        if actual is None:
            return
        if normalize_sync:
            actual = self.normalize_sync_launcher(actual)
            expected = self.normalize_sync_launcher(expected)
        if actual != expected:
            self.fail("shared_launcher", subject, "launcher_template_mismatch")
        else:
            self.pass_("shared_launcher", subject)

    def audit_agent_launcher(self) -> None:
        path = self.agents_dir / "run-agent.py"
        subject = "launcher:run-agent.py"
        self.audit_documented_launcher(
            path,
            subject,
            self.release_root
            / self.expected_sha
            / "references/scripts/run-agent.py",
            "",
            expected_mode=0o500,
        )

    def discover_lookup_only_units(self, primary_names: set[str]) -> list[str]:
        patterns = (AGENT_UNIT, SYNC_UNIT, FEISHU_UNIT, TODO_UNIT, JOIN_UNIT)
        matches: set[str] = set()
        transient = (
            self.runtime_dir / "systemd/transient"
            if self.runtime_dir is not None
            else None
        )
        for index, root in enumerate(self.unit_search_directories()):
            if root == self.units_dir or root == transient:
                continue
            if not root.exists() and not root.is_symlink():
                continue
            try:
                scanned = 0
                metadata = root.lstat()
                scan_root = root.resolve(strict=True)
                scan_metadata = scan_root.lstat()
                if (
                    not stat.S_ISDIR(scan_metadata.st_mode)
                    or scan_metadata.st_uid not in {0, os.geteuid()}
                    or bool(stat.S_IMODE(scan_metadata.st_mode) & 0o022)
                    or (
                        not stat.S_ISDIR(metadata.st_mode)
                        and not stat.S_ISLNK(metadata.st_mode)
                    )
                ):
                    raise OSError("unsafe lookup directory")
                with os.scandir(scan_root) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > MAX_DISCOVERY_ENTRIES:
                            raise OSError("too many lookup entries")
                        name = entry.name
                        service_name = (
                            Path(name).with_suffix(".service").name
                            if name.endswith(".timer")
                            else name
                        )
                        if not any(pattern.fullmatch(service_name) for pattern in patterns):
                            continue
                        if name in primary_names:
                            continue
                        matches.add(name)
                        if len(matches) > MAX_DISCOVERY_ENTRIES:
                            raise OSError("too many lookup-only units")
            except OSError:
                self.unknown(
                    "inventory",
                    f"systemd-lookup:{index}",
                    "unit_lookup_inventory_unreadable",
                )
        if self._verify_user_manager:
            child_env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
            if self.runtime_dir is not None:
                child_env["XDG_RUNTIME_DIR"] = str(self.runtime_dir)
            try:
                done, unit_files_output = run_bounded_text(
                    [
                        "/usr/bin/systemctl",
                        "--user",
                        "list-unit-files",
                        "--no-legend",
                        "--no-pager",
                        "--plain",
                        "--full",
                        "buzz-local-*.service",
                        "gitlab-buzz-sync-*.service",
                        "buzz-feishu-*.service",
                        "gitlab-todo-sync-*.service",
                        "buzz-agent-join.service",
                        "gitlab-buzz-sync-*.timer",
                        "buzz-feishu-*.timer",
                        "gitlab-todo-sync-*.timer",
                        "buzz-agent-join.timer",
                        "buzz-local-*.timer",
                    ],
                    env=child_env,
                    timeout=30,
                )
                if done.returncode != 0:
                    raise OSError("effective inventory unavailable")
                manager_outputs = [unit_files_output]
                loaded, loaded_output = run_bounded_text(
                    [
                        "/usr/bin/systemctl",
                        "--user",
                        "list-units",
                        "--all",
                        "--no-legend",
                        "--no-pager",
                        "--plain",
                        "--full",
                        "buzz-local-*.service",
                        "gitlab-buzz-sync-*.service",
                        "buzz-feishu-*.service",
                        "gitlab-todo-sync-*.service",
                        "buzz-agent-join.service",
                        "buzz-local-*.timer",
                        "gitlab-buzz-sync-*.timer",
                        "buzz-feishu-*.timer",
                        "gitlab-todo-sync-*.timer",
                        "buzz-agent-join.timer",
                    ],
                    env=child_env,
                    timeout=30,
                )
                if loaded.returncode != 0:
                    raise OSError("loaded unit inventory unavailable")
                manager_outputs.append(loaded_output)
                for line in "\n".join(manager_outputs).splitlines():
                    fields = line.split()
                    if not fields:
                        continue
                    name = fields[0]
                    service_name = (
                        Path(name).with_suffix(".service").name
                        if name.endswith(".timer")
                        else name
                    )
                    if (
                        any(pattern.fullmatch(service_name) for pattern in patterns)
                        and name not in primary_names
                        and not (
                            transient is not None
                            and ((transient / name).exists() or (transient / name).is_symlink())
                        )
                    ):
                        matches.add(name)
            except (OSError, subprocess.TimeoutExpired):
                self.unknown(
                    "inventory", "systemd", "effective_unit_inventory_unavailable"
                )
        for name in sorted(matches):
            self.unknown(
                "inventory",
                f"lookup-unit:{name}",
                "unit_outside_primary_directory",
            )
        return sorted(matches)

    def discover_transient_services(self) -> list[Path]:
        if self.runtime_dir is None:
            return []
        transient = self.runtime_dir / "systemd/transient"
        try:
            transient_meta = transient.lstat()
            transient_ok = (
                stat.S_ISDIR(transient_meta.st_mode)
                and not stat.S_ISLNK(transient_meta.st_mode)
                and transient.resolve(strict=True) == transient
            )
        except FileNotFoundError:
            return []
        except OSError:
            self.unknown("inventory", "transient_systemd", "transient_inventory_unreadable")
            return []
        if not transient_ok:
            self.unknown("inventory", "transient_systemd", "transient_inventory_untrusted")
            return []
        try:
            candidates: list[Path] = []
            scanned = 0
            with os.scandir(transient) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > MAX_DISCOVERY_ENTRIES:
                        self.unknown(
                            "inventory",
                            "transient_systemd",
                            "transient_inventory_too_large",
                        )
                        break
                    if entry.name.endswith((".service", ".timer")):
                        candidates.append(Path(entry.path))
        except OSError:
            self.unknown("inventory", "transient_systemd", "transient_inventory_unreadable")
            return []
        candidates.sort()
        patterns = (AGENT_UNIT, SYNC_UNIT, FEISHU_UNIT, TODO_UNIT, JOIN_UNIT)
        matches = []
        for path in candidates:
            service_name = (
                path.with_suffix(".service").name
                if path.name.endswith(".timer")
                else path.name
            )
            if any(pattern.fullmatch(service_name) for pattern in patterns):
                matches.append(path)
        for path in matches:
            # Transient unit files can contain the complete inherited environment.
            # Deliberately report only their public unit name and never read content.
            self.unknown(
                "inventory",
                f"transient:{path.name}",
                "transient_unit_content_not_inspected",
            )
        return matches

    def run(self) -> dict[str, Any]:
        expected_release = self.release_root / self.expected_sha
        if self.validate_release_manifest(expected_release):
            self.pass_("release_integrity", "target-release", "manifest_verified")
        else:
            self.fail("release_integrity", "target-release", "release_manifest_invalid")
        try:
            units_meta = self.units_dir.lstat()
            units_dir_ok = (
                stat.S_ISDIR(units_meta.st_mode)
                and not stat.S_ISLNK(units_meta.st_mode)
                and self.units_dir.resolve(strict=True) == self.units_dir
            )
        except OSError:
            units_dir_ok = False
        if not units_dir_ok:
            self.unknown("inventory", "systemd", "unit_directory_missing")
            units: list[Path] = []
            timers: list[Path] = []
        else:
            units = []
            timers = []
            try:
                scanned = 0
                with os.scandir(self.units_dir) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > MAX_DISCOVERY_ENTRIES:
                            self.unknown("inventory", "systemd", "unit_inventory_too_large")
                            break
                        if entry.name.endswith(".service"):
                            units.append(Path(entry.path))
                        elif entry.name.endswith(".timer"):
                            timers.append(Path(entry.path))
            except OSError:
                self.unknown("inventory", "systemd", "unit_inventory_unreadable")
            units.sort()
            timers.sort()
        agents: list[tuple[Path, str]] = []
        sync: list[tuple[Path, str]] = []
        feishu: list[tuple[Path, str]] = []
        todo: list[tuple[Path, str]] = []
        joins: list[tuple[Path, str]] = []
        for unit in units:
            for pattern, target in (
                (AGENT_UNIT, agents),
                (SYNC_UNIT, sync),
                (FEISHU_UNIT, feishu),
                (TODO_UNIT, todo),
                (JOIN_UNIT, joins),
            ):
                match = pattern.fullmatch(unit.name)
                if match:
                    target.append((unit, match.group(1)))
                    break
        service_names = {path.name for path in units}
        persistent_timer_names: list[str] = []
        for timer in timers:
            service_name = timer.with_suffix(".service").name
            if AGENT_UNIT.fullmatch(service_name):
                persistent_timer_names.append(timer.name)
                self.unknown(
                    "inventory", f"timer:{timer.name}", "unexpected_agent_timer"
                )
                continue
            if any(pattern.fullmatch(service_name) for pattern in (SYNC_UNIT, FEISHU_UNIT, TODO_UNIT, JOIN_UNIT)):
                persistent_timer_names.append(timer.name)
                if service_name not in service_names:
                    self.unknown("inventory", f"timer:{timer.name}", "orphan_timer_without_service")
        lookup_only = self.discover_lookup_only_units(
            {path.name for path in [*units, *timers]}
        )
        if not agents:
            self.unknown("inventory", "agents", "no_agent_services_discovered")
        else:
            self.load_prompt_roles({agent for _unit, agent in agents})
        for unit, agent in agents:
            self.audit_agent(unit, agent)
        if agents:
            self.audit_agent_launcher()
        for unit, name in sync:
            self.audit_sync(unit, name)
        if sync:
            self.audit_documented_launcher(
                self.home / ".config/buzz/sync/gitlab-buzz-sync-launch.sh",
                "launcher:gitlab-buzz-sync-launch.sh",
                Path(__file__).resolve().parents[1] / "references/systemd/README.md",
                "sync-timer-launcher",
                normalize_sync=True,
            )
        else:
            self.add("sync_release", "inventory", "not_applicable", "not_deployed")
        for unit, name in feishu:
            self.audit_direct_release_unit(unit, f"feishu:{name}", "feishu_release", "buzz_feishu_group_sync.py")
        if not feishu:
            self.add("feishu_release", "inventory", "not_applicable", "not_deployed")
        for unit, name in todo:
            self.audit_direct_release_unit(unit, f"todo:{name}", "todo_release", "gitlab_todo_sync.py")
        if todo:
            self.audit_documented_launcher(
                self.home / ".config/buzz/todo/gitlab-todo-sync-launch.sh",
                "launcher:gitlab-todo-sync-launch.sh",
                Path(__file__).resolve().parents[1]
                / "references/systemd/personal-todo-sync.md",
                "todo-timer-launcher",
            )
        else:
            self.add("todo_release", "inventory", "not_applicable", "not_deployed")
        for unit, name in joins:
            self.audit_direct_release_unit(
                unit,
                name,
                "join_release",
                "buzz_agent_join_requests.py",
            )
        if joins:
            self.audit_join_timer_runtime()
        if not joins:
            managed_agents = sorted(
                agent
                for _unit, agent in agents
                if self.prompt_roles.get(agent) in JOIN_MANAGED_PROMPT_ROLES
            )
            if managed_agents:
                self.fail(
                    "join_release",
                    "inventory",
                    "required_service_not_deployed",
                )
            else:
                self.add("join_release", "inventory", "not_applicable", "not_deployed")
        self.audit_harnesses()
        transient = self.discover_transient_services()
        counts = {status: sum(item["status"] == status for item in self.checks) for status in ("pass", "fail", "unknown", "not_applicable")}
        gaps: dict[str, dict[str, int | str]] = {}
        for gap_id in GAP_IDS:
            evidence = [item for item in self.checks if gap_id in item["gap_ids"]]
            gap_counts = {
                status: sum(item["status"] == status for item in evidence)
                for status in ("pass", "fail", "unknown", "not_applicable")
            }
            if gap_counts["fail"]:
                status = "fail"
            elif gap_counts["unknown"]:
                status = "unknown"
            elif gap_counts["pass"]:
                status = "pass"
            else:
                status = "not_applicable"
            gaps[gap_id] = {"status": status, **gap_counts}
        ok = counts["fail"] == 0 and counts["unknown"] == 0
        return {
            "ok": ok,
            "expected_sha": self.expected_sha,
            "inventory": {
                "agents": len(agents),
                "sync_services": len(sync),
                "feishu_services": len(feishu),
                "todo_services": len(todo),
                "join_services": len(joins),
                "harnesses": len(self.harnesses),
                "buzz_cli_binaries": len(self.cli_paths),
                "transient_services": len(transient),
                "lookup_only_units": len(lookup_only),
            },
            "inventory_ids": {
                "agents": sorted(name for _unit, name in agents),
                "agent_services": sorted(unit.name for unit, _name in agents),
                "sync_services": sorted(unit.name for unit, _name in sync),
                "feishu_services": sorted(unit.name for unit, _name in feishu),
                "todo_services": sorted(unit.name for unit, _name in todo),
                "join_services": sorted(unit.name for unit, _name in joins),
                "persistent_timers": sorted(persistent_timer_names),
                "harnesses": sorted(
                    self.public_harness_identity(kind, value)
                    for kind, value in self.harnesses
                ),
                "buzz_cli_binaries": self.public_cli_identities(),
                "transient_units": sorted(path.name for path in transient),
                "lookup_only_units": sorted(lookup_only),
            },
            "summary": counts,
            "gaps": gaps,
            "checks": self.checks,
        }


def audit_home(home: Path, expected_sha: str) -> dict[str, Any]:
    path = Path(home)
    if not path.is_absolute():
        raise ValueError("home must be absolute")
    if SHA40.fullmatch(expected_sha) is None:
        raise ValueError("expected SHA must be 40 lowercase hex characters")
    runtime_dir: Path | None = None
    try:
        if path.resolve() == Path.home().resolve():
            raw_runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.geteuid()}")
            candidate = Path(raw_runtime)
            if candidate.is_absolute():
                runtime_dir = candidate
    except OSError:
        runtime_dir = None
    return Auditor(path, expected_sha, runtime_dir).run()


def render_text(report: dict[str, Any]) -> str:
    inventory = report["inventory"]
    summary = report["summary"]
    lines = [
        f"local Buzz alignment: {'PASS' if report['ok'] else 'FAIL'}",
        f"target: {report['expected_sha']}",
        "inventory: " + ", ".join(f"{key}={value}" for key, value in inventory.items()),
        "checks: " + ", ".join(f"{key}={value}" for key, value in summary.items()),
        "gaps: " + ", ".join(
            f"{gap_id}={value['status']}" for gap_id, value in report["gaps"].items()
        ),
    ]
    for item in report["checks"]:
        if item["status"] in {"fail", "unknown"}:
            suffix = f" ({item['detail']})" if item.get("detail") else ""
            lines.append(f"- {item['status'].upper()} {item['category']} {item['subject']}: {item['code']}{suffix}")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if not sys.flags.isolated or any(
        key in os.environ for key in AUDIT_FORBIDDEN_PARENT_ENV
    ):
        print(
            "local Buzz alignment: ERROR\nrun from a clean environment with /usr/bin/python3 -I",
            file=sys.stderr,
        )
        return 2
    args = parse_args(argv)
    try:
        report = audit_home(args.home, args.expected_sha)
    except ValueError as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else f"local Buzz alignment: ERROR\n{exc}")
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(render_text(report))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
