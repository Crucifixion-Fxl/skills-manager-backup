#!/usr/bin/python3 -I
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Build the immutable content manifest for one extracted Buzz skill release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import tempfile


SHA40 = re.compile(r"[0-9a-f]{40}")
OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
MANIFEST = ".release-manifest.json"
MAX_FILES = 8192
MAX_RELEASE_BYTES = 256 * 1024 * 1024
MAX_TREE_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def trusted_directory_chain(path: Path, uid: int) -> bool:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    for candidate in (resolved, *resolved.parents):
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


def limit_file_size(max_bytes: int) -> None:
    _soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    cap = max_bytes if hard == resource.RLIM_INFINITY else min(max_bytes, hard)
    resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))


def git_output(
    source_repo: Path,
    child_env: dict[str, str],
    arguments: list[str],
    max_bytes: int,
    timeout: int = 30,
) -> bytes:
    with tempfile.TemporaryFile() as output:
        completed = subprocess.run(
            [
                "/usr/bin/git",
                "--no-replace-objects",
                "-C",
                str(source_repo),
                *arguments,
            ],
            env=child_env,
            stdout=output,
            stderr=subprocess.DEVNULL,
            preexec_fn=lambda: limit_file_size(max_bytes),
            timeout=timeout,
            check=False,
        )
        output.seek(0)
        payload = output.read(max_bytes + 1)
    if completed.returncode != 0 or len(payload) > max_bytes:
        raise ValueError("Git object output is unavailable or oversized")
    return payload


def write_blob(destination: Path, relative: str, contents: bytes, executable: bool) -> None:
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    current = target.parent
    while current != destination:
        current.chmod(0o755)
        current = current.parent
    descriptor = os.open(
        target,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(contents):
            written = os.write(descriptor, contents[offset:])
            if written <= 0:
                raise OSError("short release file write")
            offset += written
        os.fchmod(descriptor, 0o755 if executable else 0o644)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def archive_records(
    source_repo: Path,
    commit: str,
    destination: Path | None = None,
) -> dict[str, dict[str, object]]:
    source_repo = source_repo.resolve(strict=True)
    metadata = source_repo.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or not trusted_directory_chain(source_repo, os.geteuid())
    ):
        raise ValueError("source repository is not trusted")
    child_env = {
        "HOME": "/nonexistent",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ATTR_NOSYSTEM": "1",
    }
    resolved = git_output(
        source_repo,
        child_env,
        ["rev-parse", "--verify", f"{commit}^{{commit}}"],
        1024,
    )
    if resolved.decode("ascii", "strict").strip() != commit:
        raise ValueError("commit is not present in the source repository")
    git_output(
        source_repo,
        child_env,
        ["fsck", "--strict", "--no-dangling", "--no-reflogs", commit],
        MAX_TREE_BYTES,
        120,
    )
    tree = git_output(
        source_repo,
        child_env,
        ["ls-tree", "-rz", "--full-tree", commit, "--", "skills/buzz-agent-setup"],
        MAX_TREE_BYTES,
        60,
    )
    records: dict[str, dict[str, object]] = {}
    extracted_bytes = 0
    prefix = "skills/buzz-agent-setup/"
    for raw_entry in tree.split(b"\0"):
        if not raw_entry:
            continue
        try:
            raw_meta, raw_name = raw_entry.split(b"\t", 1)
            raw_mode, raw_type, raw_oid = raw_meta.split(b" ", 2)
            name = raw_name.decode("utf-8")
            oid = raw_oid.decode("ascii")
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("source tree entry is invalid") from error
        if raw_type != b"blob" or raw_mode not in {b"100644", b"100755"}:
            raise ValueError("source tree contains an unsupported entry")
        if not name.startswith(prefix):
            raise ValueError("source tree path escaped the Skill")
        relative = name.removeprefix(prefix)
        relative_path = Path(relative)
        if (
            not relative
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative in records
            or OBJECT_ID.fullmatch(oid) is None
        ):
            raise ValueError("source tree path or object id is invalid")
        contents = git_output(
            source_repo,
            child_env,
            ["cat-file", "blob", oid],
            MAX_FILE_BYTES,
        )
        extracted_bytes += len(contents)
        if extracted_bytes > MAX_RELEASE_BYTES:
            raise ValueError("source tree exceeds the release limit")
        records[relative] = {
            "sha256": hashlib.sha256(contents).hexdigest(),
            "mode": 0o555 if raw_mode == b"100755" else 0o444,
        }
        if destination is not None:
            write_blob(destination, relative, contents, raw_mode == b"100755")
        if len(records) > MAX_FILES:
            raise ValueError("source tree contains too many files")
    return records


def materialize(root: Path, commit: str, source_repo: Path) -> dict[str, object]:
    if SHA40.fullmatch(commit) is None:
        raise ValueError("commit must be a full SHA")
    root = root.resolve(strict=True)
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or any(root.iterdir())
    ):
        raise ValueError("extraction root must be an empty owner-only directory")
    files = archive_records(source_repo, commit, root)
    if not files:
        raise ValueError("source tree is empty")
    return {"version": 1, "commit": commit, "files": files}


def build(root: Path, commit: str, source_repo: Path) -> dict[str, object]:
    if SHA40.fullmatch(commit) is None:
        raise ValueError("commit must be a full SHA")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("release must be a directory")
    files: dict[str, dict[str, object]] = {}
    release_bytes = 0
    scanned = 0
    for path in root.rglob("*"):
        scanned += 1
        if scanned > MAX_FILES * 2:
            raise ValueError("release contains too many entries")
        relative = path.relative_to(root).as_posix()
        if relative == MANIFEST:
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("release cannot contain symlinks")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("release contains a non-regular file")
        if metadata.st_size > MAX_FILE_BYTES:
            raise ValueError("release contains an oversized file")
        release_bytes += metadata.st_size
        if release_bytes > MAX_RELEASE_BYTES:
            raise ValueError("release is too large")
        files[relative] = {
            "sha256": digest(path),
            # Publication immediately removes every write bit.  Record the
            # final immutable mode so the installed release can be audited.
            "mode": stat.S_IMODE(metadata.st_mode) & ~0o222,
        }
        if len(files) > MAX_FILES:
            raise ValueError("release contains too many files")
    if files != archive_records(source_repo, commit):
        raise ValueError("release content does not match the source commit")
    return {"version": 1, "commit": commit, "files": files}


def write(root: Path, payload: dict[str, object]) -> None:
    target = root / MANIFEST
    if target.exists() or target.is_symlink():
        raise ValueError("release manifest already exists")
    descriptor, raw = tempfile.mkstemp(prefix=".release-manifest.", dir=root)
    temporary = Path(raw)
    try:
        os.fchmod(descriptor, 0o600)
        data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("short manifest write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, target)
        target.chmod(0o444)
        directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--extract", action="store_true")
    args = parser.parse_args()
    try:
        payload = (
            materialize(args.release, args.commit, args.source_repo)
            if args.extract
            else build(args.release, args.commit, args.source_repo)
        )
        write(args.release.resolve(strict=True), payload)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
