"""release 合流的第二道完整性检查：找出 MR 合入的其他长期分支提交（gitlab-mr Step 2.87）。

预合并的合流分支已经包含 target，target <- head 一份报告看不到 target 历史里按行的部分丢失；
因此对 MR first-parent 合并里"不在 target 中、却属于另一条长期分支"的父提交 P，
还要求 `<target_sha> <P>` 的报告。
"""

from __future__ import annotations

import re
import subprocess

LONG_LIVED_TARGET = re.compile(r"^(main|master|staging|release/.+|hotfix/.+)$")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], text=True, capture_output=True, check=False)


def _ancestor(a: str, b: str) -> bool:
    return _run("merge-base", "--is-ancestor", a, b).returncode == 0


def long_lived_tips(error: type[Exception]) -> list[str]:
    result = _run("ls-remote", "--heads", "origin")
    if result.returncode != 0:
        raise error(f"git ls-remote origin failed: {result.stderr.strip()}")
    tips = []
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if LONG_LIVED_TARGET.match(ref.removeprefix("refs/heads/")):
            if _run("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
                raise error(f"{ref} ({sha[:12]}) is not available locally; fetch the long-lived branches first")
            tips.append(sha)
    return tips


def sync_parents(target_sha: str, head_sha: str, error: type[Exception]) -> list[str]:
    """Parents of first-parent merges in target..head that were a tip of a long-lived branch.

    "Was a tip" = on that branch's first-parent chain, and not already in the target. For a merge that brings the
    target in (sync-in, e.g. master merged into the release being promoted) the source's own branch counts too;
    otherwise branches that already contain the MR head are the source itself and are skipped. Only the newest
    such commit per line of history is returned (older ones are covered by its report).
    """
    merges = _run("rev-list", "--first-parent", "--merges", f"{target_sha}..{head_sha}").stdout.split()
    if not merges:
        return []
    tips = long_lived_tips(error)
    chains = [set(_run("rev-list", "--first-parent", tip).stdout.split()) for tip in tips]
    other = [c for tip, c in zip(tips, chains) if not _ancestor(head_sha, tip)]
    found: set[str] = set()
    for merge in merges:
        parents = _run("log", "-1", "--format=%P", merge).stdout.split()
        outside = [p for p in parents if not _ancestor(p, target_sha)]
        pool = chains if len(outside) < len(parents) else other  # sync-in merge: the source's own line counts too
        found.update(p for p in outside if any(p in chain for chain in pool))
    return sorted(p for p in found if not any(p != q and _ancestor(p, q) for q in found))
