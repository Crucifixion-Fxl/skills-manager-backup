#!/usr/bin/python3 -I
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Refresh the trusted, full-history local mirror of the canonical Skills repo.

``audit_local_alignment.py``'s ``plugin_revision`` check (ADR-0024) needs to
prove a harness's installed commit *descends from* the pinned release SHA,
not just equals it -- some plugin-marketplace mechanisms have no way to pin a
historical commit and always track their remote's current tip. Proving
descent needs real git ancestry (``merge-base --is-ancestor``), which needs
full history; the audit itself must stay offline and read-only, so it never
fetches. This script is the one place, run out-of-band by the owner (not by
the audit, not automatically), that is allowed to talk to the network on the
audit's behalf: it keeps one root/owner-only bare mirror in sync with the
canonical remote so the audit can answer ancestry questions purely locally.

This is not a release: it carries no working tree, is never treated as a
release install, and its content is never executed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


CANONICAL_SKILLS_REMOTE = (
    "git@" + ".".join(("gitlab", "addx", "ai")) + ":engineering/skills.git"
)
MIRROR_TIMEOUT = 300


def trusted_directory_chain(path: Path, uid: int) -> bool:
    """Like audit_local_alignment.py's helper of the same name, except a
    not-yet-created component is skipped rather than treated as untrusted:
    unlike the read-only audit, this script creates directories itself, with
    safe permissions, as it goes -- a component that doesn't exist yet isn't
    evidence of anything."""
    for candidate in (path, *path.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
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


def trusted_private_directory(path: Path, uid: int) -> bool:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    return bool(
        resolved == path
        and stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == uid
        and not bool(stat.S_IMODE(metadata.st_mode) & 0o077)
        and trusted_directory_chain(resolved.parent, uid)
    )


def child_env(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def run(argv: list[str], *, env: dict[str, str], timeout: int, cwd: Path | None = None) -> None:
    completed = subprocess.run(
        argv,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise OSError(
            f"{argv[0]} exited {completed.returncode}: "
            + completed.stderr.decode("utf-8", "replace")[:2000]
        )


def existing_remote(mirror: Path, env: dict[str, str]) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "--git-dir", str(mirror), "config", "--get", "remote.origin.url"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise OSError("mirror has no readable remote.origin.url")
    return completed.stdout.decode("utf-8", "replace").strip()


def refresh(home: Path) -> dict[str, object]:
    uid = os.geteuid()
    mirror_root = home / ".local/share/buzz-agent-setup/git-mirror"
    mirror = mirror_root / "skills.git"
    if not trusted_directory_chain(mirror_root.parent, uid):
        raise ValueError("untrusted git-mirror parent directory chain")
    # os.makedirs() only applies `mode` to the leaf it creates, not to any
    # missing intermediate directories -- those get the ambient umask's
    # default, which may be far looser than owner-only. Force owner-only for
    # every directory this call creates, regardless of the caller's umask.
    old_umask = os.umask(0o077)
    try:
        os.makedirs(mirror_root, mode=0o700, exist_ok=True)
    finally:
        os.umask(old_umask)
    os.chmod(mirror_root, 0o700)
    if not trusted_directory_chain(mirror_root, uid):
        raise ValueError("untrusted git-mirror directory")
    env = child_env(home)
    created = False
    if mirror.exists() or mirror.is_symlink():
        if not trusted_private_directory(mirror, uid):
            raise ValueError("existing git mirror is not a trusted owner-only directory; refusing to touch it")
        remote = existing_remote(mirror, env)
        if remote != CANONICAL_SKILLS_REMOTE:
            raise ValueError(
                "existing git mirror's remote is not the canonical Skills repo; "
                "refusing to silently rebase trust onto a different tree"
            )
        run(
            [
                "/usr/bin/git", "--git-dir", str(mirror), "fetch", "--prune", "origin",
                "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*",
            ],
            env=env,
            timeout=MIRROR_TIMEOUT,
        )
    else:
        old_umask = os.umask(0o077)
        tmp = None
        try:
            tmp = Path(tempfile.mkdtemp(dir=mirror_root, prefix=".mirror-"))
            target = tmp / "skills.git"
            run(
                ["/usr/bin/git", "clone", "--mirror", CANONICAL_SKILLS_REMOTE, str(target)],
                env=env,
                timeout=MIRROR_TIMEOUT,
            )
            if not trusted_private_directory(target, uid):
                raise ValueError("freshly cloned mirror failed its own trust check")
            os.replace(target, mirror)
            created = True
        finally:
            os.umask(old_umask)
            if tmp is not None and tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
    if not trusted_private_directory(mirror, uid):
        raise ValueError("git mirror failed its post-refresh trust check")
    head_sha = subprocess.run(
        ["/usr/bin/git", "--git-dir", str(mirror), "rev-parse", "refs/heads/main"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    main_sha = head_sha.stdout.decode("utf-8", "replace").strip() if head_sha.returncode == 0 else None
    return {"ok": True, "mirror": str(mirror), "created": created, "main": main_sha}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    args = parser.parse_args(argv)
    try:
        home = args.home
        if not home.is_absolute() or home.resolve(strict=True) != home:
            raise ValueError("--home must be an absolute, canonical path")
        result = refresh(home)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
