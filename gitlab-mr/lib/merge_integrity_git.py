"""check_merge_integrity 的 Git 访问层：顶层运行、NUL 分隔、按 tree 批量读取 blob。"""


from __future__ import annotations

import os
import re
import subprocess
import sys
from functools import lru_cache

class GitError(RuntimeError):
    pass


_ROOT: str | None = None


def set_root(path: str) -> None:
    """Run every later git call from the repository top level."""
    global _ROOT
    _ROOT = path


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "surrogateescape")


def git_bytes(*args: str, check: bool = True, ok_codes: tuple[int, ...] = (0,)) -> tuple[int, bytes]:
    cmd = [
        "git", "--no-pager", "--literal-pathspecs",
        "-c", "core.quotePath=false", "-c", "diff.relative=false", "-c", "diff.renames=false",
        "-c", "merge.renames=true", "-c", "log.follow=false", "-c", "diff.external=", "-c", "color.ui=false",
    ]
    if _ROOT:
        cmd += ["-C", _ROOT]
    result = subprocess.run(cmd + list(args), capture_output=True, env={**os.environ, "LC_ALL": "C"})
    if check and result.returncode not in ok_codes:
        raise GitError(f"git {' '.join(args)}: {_decode(result.stderr).strip()}")
    return result.returncode, result.stdout


def git(*args: str, check: bool = True) -> str:
    return _decode(git_bytes(*args, check=check)[1])


def nul_list(data: bytes) -> list[str]:
    return [_decode(item) for item in data.split(b"\0") if item]


def changed(a: str, b: str) -> set[str]:
    return set(nul_list(git_bytes("diff", "--no-renames", "--no-ext-diff", "--name-only", "-z", a, b)[1]))


@lru_cache(maxsize=256)
def tree_index(ref: str) -> dict[str, str]:
    """path -> object id for every blob/link in ref's tree (one subprocess per ref)."""
    out: dict[str, str] = {}
    for entry in git_bytes("ls-tree", "-r", "-z", "--full-tree", ref)[1].split(b"\0"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        out[_decode(path)] = _decode(meta.split()[2])
    return out


def blob(ref: str, path: str) -> str:
    return tree_index(ref).get(path, "-")


def rev(ref: str) -> str:
    code, out = git_bytes("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", check=False)
    if code != 0:
        raise GitError(f"unknown commit: {ref}")
    return _decode(out).strip()


def short(sha: str) -> str:
    return sha[:10]


def describe(sha: str) -> str:
    return git("log", "-1", "--format=%h %an %ci | %s", sha).strip()


def simulate(ours: str, theirs: str) -> tuple[str, set[str]]:
    """Return (merge result tree, conflicted paths)."""
    code, out = git_bytes("merge-tree", "--write-tree", "--name-only", "-z", ours, theirs, ok_codes=(0, 1))
    parts = out.split(b"\0")
    tree = _decode(parts[0]).strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", tree):
        raise GitError(f"git merge-tree {ours} {theirs}: unexpected output")
    conflicts: set[str] = set()
    if code == 1:
        for item in parts[1:]:
            if not item:
                break
            conflicts.add(_decode(item))
    return tree, conflicts


@lru_cache(maxsize=4096)
def content_lines(oid: str) -> tuple[bytes, ...] | None:
    """Lines of a blob; None for a missing path or binary content (compare by object id instead)."""
    if oid == "-":
        return None
    data = git_bytes("cat-file", "blob", oid)[1]
    return None if b"\0" in data else tuple(data.splitlines())


def renames(a: str, b: str) -> dict[str, set[str]]:
    """Rename partners between two commits (both directions), for matching a path across sides."""
    out: dict[str, set[str]] = {}
    items = nul_list(git_bytes("diff", "-M", "--no-ext-diff", "--name-status", "-z", a, b)[1])
    i = 0
    while i < len(items):
        status = items[i]
        if status.startswith(("R", "C")) and i + 2 < len(items):
            old, new = items[i + 1], items[i + 2]
            out.setdefault(old, set()).add(new)
            out.setdefault(new, set()).add(old)
            i += 3
        else:
            i += 2
    return out


def is_shallow() -> bool:
    return git("rev-parse", "--is-shallow-repository").strip() == "true"


def authored(rng: str, path: str) -> bool:
    """True when a non-merge commit in rng touched path."""
    return bool(git("log", "--no-merges", "--format=%h", "-1", rng, "--", path).strip())


def git_version() -> tuple[int, int]:
    match = re.search(r"(\d+)\.(\d+)", git("version"))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)
