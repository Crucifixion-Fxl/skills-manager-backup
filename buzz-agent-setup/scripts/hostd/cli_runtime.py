"""Portable product CLI runtime; no identity, version pin or credential fallback.

Resolution discovers paths only. Validate lazily before real subprocess use;
HTTP-native adapters do not need Node or an installed CLI. Environment values
are startup configuration, never inferred from an app credential profile.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
from typing import Mapping

NODE_ENV = 'HOSTD_NODE_BINARY'
ENTRY_ENV = 'HOSTD_LARK_CLI_ENTRY'
NOTICE = ('本机 CLI 运行路径尚未安全确认，请求未开始。\n'
          '怎么解决：明确配置 HOSTD_NODE_BINARY 和 HOSTD_LARK_CLI_ENTRY 的绝对路径；检查文件与父目录归当前用户或 root 所有、无软链接、无组或其他用户写权限，Node 可执行、CLI 入口可读取。\n'
          '复制给 AI：帮我检查 hostd 的显式 Node/CLI 路径与文件权限；不要切换应用身份，不要输出凭据，不要固定 CLI 版本或哈希。')


class RuntimePathError(ValueError):
    definite = True
    dispatched = False
    def __init__(self): super().__init__(NOTICE)


def _absolute(value):
    if (not isinstance(value, str) or not value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or not Path(value).is_absolute() or any(part in ('.', '..') for part in value.split('/'))):
        raise RuntimePathError()
    return value


def default_paths(environ=None):
    """Discover this process owner's installation without opening executables."""
    env = dict(os.environ) if environ is None else dict(environ)
    home = Path(env.get('HOME') or Path.home())
    node = shutil.which('node', path=env.get('PATH', os.defpath)) or '/usr/bin/node'
    prefix = env.get('NPM_CONFIG_PREFIX') or env.get('npm_config_prefix')
    prefixes = ([Path(prefix)] if prefix else []) + [home / '.npm-global', home / '.local', Path('/usr/local'), Path('/usr')]
    candidates = [p / 'lib/node_modules/@larksuite/cli/scripts/run.js' for p in prefixes]
    entry = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    return RuntimePaths(str(node), str(entry))


@dataclass(frozen=True)
class RuntimePaths:
    node_binary: str
    lark_cli_entry: str

    @classmethod
    def resolve(cls, environ: Mapping[str, str] | None = None):
        env = dict(os.environ)
        if environ is not None: env.update(environ)
        defaults = default_paths(env)
        return cls(_absolute(env.get(NODE_ENV, defaults.node_binary)),
                   _absolute(env.get(ENTRY_ENV, defaults.lark_cli_entry)))

    @staticmethod
    def _validate_file(raw, *, executable):
        path = Path(_absolute(raw))
        directory = leaf = None
        def trusted(meta, kind):
            return kind(meta.st_mode) and meta.st_uid in (0, os.geteuid()) and not meta.st_mode & 0o022
        try:
            directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            if not trusted(os.fstat(directory), stat.S_ISDIR): raise RuntimePathError()
            for component in path.parts[1:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                os.close(directory); directory = child
                if not trusted(os.fstat(directory), stat.S_ISDIR): raise RuntimePathError()
            leaf = os.open(path.name, getattr(os, 'O_PATH', os.O_RDONLY) | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
            meta = os.fstat(leaf)
            if not trusted(meta, stat.S_ISREG): raise RuntimePathError()
            if executable and not meta.st_mode & 0o111: raise RuntimePathError()
            if not os.access(path, os.X_OK if executable else os.R_OK, effective_ids=True): raise RuntimePathError()
        except (OSError, ValueError):
            raise RuntimePathError() from None
        finally:
            if leaf is not None: os.close(leaf)
            if directory is not None: os.close(directory)

    def validate(self):
        """Metadata-only ownership/mode/nosymlink checks; execute nothing."""
        self._validate_file(self.node_binary, executable=True)
        self._validate_file(self.lark_cli_entry, executable=False)
        return self
