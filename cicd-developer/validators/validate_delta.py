#!/usr/bin/env python3
"""Compare strict validator findings against a freshly proven Git target.

``validate.sh`` remains the strict, policy-authoritative validator. This
wrapper exists only for a committed-clean repository that already has
unrelated historical validator debt. It binds the merge target to a fresh,
config-isolated remote fetch, materializes inert committed snapshots, runs the
complete strict suite on both snapshots, and fails for every candidate-only
finding or carried finding on a candidate-changed path.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ContextManager

from _namespace_legacy_exceptions import (
    canonical_repository,
    repository_origin,
    same_repository,
)
from check_argocd_adoption_contract import bounded_process_output


SCRIPT_ROOT = Path(__file__).resolve().parent
STRICT_VALIDATOR = SCRIPT_ROOT / "validate.sh"
TRUSTED_GIT_HOST = "gitlab.addx.ai"
GIT_TIMEOUT_SECONDS = 30
REMOTE_GIT_TIMEOUT_SECONDS = 120
STRICT_TIMEOUT_SECONDS = 300
MAX_GIT_OUTPUT_BYTES = 1_048_576
# Git's index is a controlled internal file, not command output. Sixteen MiB
# leaves headroom for the declared 20,000-file snapshot budget while keeping
# every Git command's captured stdout under MAX_GIT_OUTPUT_BYTES.
MAX_GIT_INDEX_BYTES = 16_777_216
# The reused bounded runner applies RLIMIT_FSIZE to the whole Git process, so
# this is also a hard upper bound for the filtered proof pack, not only stdout.
MAX_REMOTE_GIT_OUTPUT_BYTES = 268_435_456
MAX_TREE_OUTPUT_BYTES = 16_777_216
MAX_STRICT_OUTPUT_BYTES = 16_777_216
MAX_STRICT_ERROR_DETAIL_BYTES = 8192
MAX_DELTA_OUTPUT_BYTES = 65_536
DELTA_OUTPUT_TRUNCATION_MARKER = "\n...[validator delta output truncated]\n"
SAFE_PASSTHROUGH_ENVIRONMENT = (
    "HOME",
    "SSH_AUTH_SOCK",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "CURL_CA_BUNDLE",
    "TMPDIR",
    "TMP",
    "TEMP",
)
MAX_SNAPSHOT_FILES = 20_000
MAX_SNAPSHOT_BLOB_BYTES = 16_777_216
MAX_SNAPSHOT_TOTAL_BYTES = 268_435_456
OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
HEADER_RE = re.compile(r"^==> (?P<validator>check_[A-Za-z0-9_]+\.py) on .+$")
OBJECT_RE = re.compile(
    r"^(?P<object>[A-Za-z][A-Za-z0-9_.-]*/[^\s:]+)(?::\s*|\s+)(?P<reason>.+)$"
)
LIST_INDEX_PATTERN = r"\[(?:0|[1-9][0-9]*)\]"
LIST_INDEX_RE = re.compile(LIST_INDEX_PATTERN)
YAML_BREADCRUMB_SEGMENT = rf"[A-Za-z_][A-Za-z0-9_-]*(?:{LIST_INDEX_PATTERN})*"
YAML_BREADCRUMB_PATTERN = (
    rf"(?:{LIST_INDEX_PATTERN})?\.{YAML_BREADCRUMB_SEGMENT}"
    rf"(?:\.{YAML_BREADCRUMB_SEGMENT})*"
)
VAULT_BREADCRUMB_PREFIX_RE = re.compile(
    rf"^(?P<breadcrumb>{YAML_BREADCRUMB_PATTERN})(?P<suffix>:\s)"
)
VAULT_BREADCRUMB_NO_RULE_RE = re.compile(
    rf"(?P<prefix>\(at )(?P<breadcrumb>{YAML_BREADCRUMB_PATTERN})"
    r"(?P<suffix>; no rule in vault-paths/rules\.yaml matches\)$)"
)
SUMMARY_RES = (
    re.compile(r"^FAIL: one or more validators failed$"),
    re.compile(r"^FAIL: \d+ vault-path violation\(s\) out of \d+ checked.*$"),
    re.compile(r"^FAIL: \d+ CloudFront violation\(s\)$"),
    re.compile(r"^FAIL: \d+ ObjectBucket contract violation\(s\)$"),
    re.compile(r"^FAIL: \d+ workload-naming violation\(s\)$"),
    re.compile(r"^FAIL: \d+ repository boundary violation\(s\)$"),
    re.compile(r"^FAIL: \d+ guarded one-shot Job violation\(s\)$"),
    re.compile(r"^FAIL: \d+ route violation\(s\)$"),
    re.compile(r"^FAIL: \d+ deploy-side image host violation\(s\)$"),
    re.compile(r"^FAIL: \d+ PushSecret violation\(s\) across \d+ PushSecret\(s\)$"),
    re.compile(r"^FAIL: \d+ namespace creation contract violation\(s\)$"),
    re.compile(r"^FAIL: \d+ namespace violation\(s\) across \d+ business object\(s\)$"),
    re.compile(r"^FAIL: \d+ S3 Bucket delete guard violation\(s\)$"),
    re.compile(r"^FAIL: \d+ Application violation\(s\) across \d+ Application\(s\)$"),
    re.compile(r"^FAIL: \d+ unsafe Sentry resource name\(s\) found$"),
    re.compile(r"^FAIL: \d+ DB resource contract violation\(s\)$"),
)


class DeltaError(RuntimeError):
    """A condition that must fail closed instead of accepting a delta."""


class BoundedReporter:
    """Write stdout and stderr under one deterministic UTF-8 byte budget."""

    def __init__(self, max_bytes: int | None = None) -> None:
        if max_bytes is None:
            max_bytes = MAX_DELTA_OUTPUT_BYTES
        marker_size = len(DELTA_OUTPUT_TRUNCATION_MARKER.encode("utf-8"))
        if max_bytes <= marker_size:
            raise DeltaError("validator delta output budget is invalid")
        self._remaining = max_bytes - marker_size
        self._truncated = False

    def write(self, value: object, *, stderr: bool = False) -> None:
        if self._truncated:
            return
        encoded = str(value).encode("utf-8", errors="replace")
        stream = sys.stderr if stderr else sys.stdout
        if len(encoded) <= self._remaining:
            stream.write(encoded.decode("utf-8"))
            self._remaining -= len(encoded)
            return
        prefix = encoded[: self._remaining].decode("utf-8", errors="ignore")
        if prefix:
            stream.write(prefix)
        stream.write(DELTA_OUTPUT_TRUNCATION_MARKER)
        self._remaining = 0
        self._truncated = True

    def line(self, value: object = "", *, stderr: bool = False) -> None:
        self.write(f"{value}\n", stderr=stderr)


class ArgumentParseExit(Exception):
    """Controlled argparse completion without unbounded writes or traceback."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class BoundedArgumentParser(argparse.ArgumentParser):
    """Route argparse help and errors through the aggregate reporter cap."""

    def __init__(self, reporter: BoundedReporter, **kwargs: object) -> None:
        self.reporter = reporter
        super().__init__(**kwargs)

    def _print_message(self, message: str | None, file: object = None) -> None:
        if message:
            self.reporter.write(message, stderr=file is sys.stderr)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            self._print_message(message, sys.stderr)
        raise ArgumentParseExit(status)


@dataclass(frozen=True, order=True)
class Finding:
    """A stable policy finding emitted by one strict validator."""

    validator: str
    relative_path: str
    obj: str
    reason: str

    def render(self) -> str:
        return f"{self.validator}: {self.relative_path}: {self.obj}: {self.reason}"


@dataclass(frozen=True)
class StrictResult:
    status: int
    findings: Counter[Finding]


@dataclass(frozen=True)
class RemoteProof:
    """One fresh remote target whose backing object store remains live."""

    root: Path
    target_commit: str
    object_format: str


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    oid: str
    path: PurePosixPath


@dataclass(frozen=True)
class CandidateContext:
    expected_origin: str
    target_branch: str
    repo: Path
    relative_scope: Path
    caller_objects: Path
    object_format: str
    candidate_commit: str
    remote_transport: str
    platform_source: Path | None
    requested_paths: tuple[Path, ...]


@dataclass(frozen=True)
class ComparisonEvidence:
    remote_target_commit: str
    base_commit: str
    base_result: StrictResult
    candidate_result: StrictResult
    candidate_write_set: set[str]


RemoteProofFactory = Callable[
    [Path, Path, str, str, str],
    ContextManager[RemoteProof],
]


def parse_args(argv: list[str], reporter: BoundedReporter) -> argparse.Namespace:
    parser = BoundedArgumentParser(
        reporter,
        prog="validate_delta.py",
        description=(
            "Run validate.sh on committed candidate and freshly proven target "
            "snapshots, then fail for candidate-only findings or carried debt "
            "on candidate-changed paths."
        ),
    )
    parser.add_argument(
        "--expected-origin",
        required=True,
        help=(
            "authoritative credential-free gitlab.addx.ai repository identity, "
            "for example https://gitlab.addx.ai/DEV/argocd-apps.git"
        ),
    )
    parser.add_argument(
        "--base-ref",
        required=True,
        help="MR target in the exact form origin/<concrete-target-branch>",
    )
    parser.add_argument(
        "--repo-context",
        choices=("auto", "app", "k8s", "argocd-apps", "crossplane-infra"),
        default="auto",
        help="forwarded unchanged to validate.sh (default: auto)",
    )
    parser.add_argument(
        "--platform-source",
        type=Path,
        help=(
            "trusted DEV/k8s component path; it must exist in both committed "
            "snapshots and is valid only with --repo-context k8s"
        ),
    )
    parser.add_argument(
        "--objectbucket-target",
        help="forwarded unchanged to validate.sh",
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="tracked directory to validate, relative to a committed-clean worktree",
    )
    return parser.parse_args(argv[1:])


def safe_process_environment(
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a minimal environment without executable caller configuration."""
    if extra is not None and set(extra) - {"GIT_INDEX_FILE"}:
        raise DeltaError("internal Git environment override is not allowlisted")
    environment = {
        key: os.environ[key]
        for key in SAFE_PASSTHROUGH_ENVIRONMENT
        if os.environ.get(key)
    }
    environment.update(
        {
            "GCM_INTERACTIVE": "never",
            "GIT_ASKPASS": "/bin/false",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": environment.get("HOME", os.devnull),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.defpath,
            "PYTHONNOUSERSITE": "1",
            "SSH_ASKPASS": "/bin/false",
            "XDG_CONFIG_HOME": os.devnull,
        }
    )
    ssh = shutil.which("ssh", path=os.defpath)
    if ssh is not None:
        environment["GIT_SSH_COMMAND"] = (
            f"{Path(ssh).resolve()} -F {os.devnull} -o BatchMode=yes "
            "-o StrictHostKeyChecking=yes -o ClearAllForwardings=yes "
            "-o PermitLocalCommand=no -o ProxyCommand=none "
            "-o UpdateHostKeys=no -o ConnectionAttempts=1 -o ConnectTimeout=30"
        )
    if extra is not None:
        environment.update(extra)
    return environment


@contextmanager
def strict_process_environment(shell: Path) -> Iterator[dict[str, str]]:
    """Expose only the already-running Python interpreter to validate.sh."""
    interpreter = Path(sys.executable)
    try:
        resolved_interpreter = interpreter.resolve(strict=True)
    except OSError as exc:
        raise DeltaError("current Python interpreter is unavailable") from exc
    if not interpreter.is_absolute() or not resolved_interpreter.is_file():
        raise DeltaError("current Python interpreter is unavailable")

    with tempfile.TemporaryDirectory(prefix="cicd-validator-python-") as temp:
        runtime_bin = Path(temp)
        launcher = runtime_bin / "python3"
        launcher.write_text(
            f"#!{shell}\nexec {shlex.quote(str(interpreter))} \"$@\"\n",
            encoding="utf-8",
        )
        launcher.chmod(0o500)
        environment = safe_process_environment()
        environment["PATH"] = f"{runtime_bin}{os.pathsep}{os.defpath}"
        yield environment


def git_executable() -> str:
    executable = shutil.which("git", path=os.defpath)
    if executable is None:
        raise DeltaError("Git dependency is unavailable")
    return str(Path(executable).resolve())


def git_process(
    git_root: Path,
    *args: str,
    description: str,
    timeout: int = GIT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_GIT_OUTPUT_BYTES,
    max_file_bytes: int | None = None,
    remote: bool = False,
    extra_environment: dict[str, str] | None = None,
    allowed_statuses: frozenset[int] = frozenset({0}),
) -> tuple[int, bytes]:
    if timeout <= 0 or max_bytes <= 0:
        raise DeltaError(f"{description} has invalid execution limits")
    file_bytes = max_bytes if max_file_bytes is None else max_file_bytes
    if file_bytes < max_bytes:
        raise DeltaError(f"{description} has invalid execution limits")
    command = [
        git_executable(),
        "-C",
        str(git_root),
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "credential.helper=",
        "-c",
        "core.askPass=/bin/false",
    ]
    if remote:
        command.extend(
            [
                "-c",
                "http.followRedirects=false",
                "-c",
                "http.sslVerify=true",
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.https.allow=always",
                "-c",
                "protocol.ssh.allow=always",
                "-c",
                "protocol.file.allow=never",
                "-c",
                "protocol.ext.allow=never",
                "-c",
                "protocol.git.allow=never",
                "-c",
                "protocol.http.allow=never",
                "-c",
                "fetch.fsckObjects=true",
                "-c",
                "transfer.fsckObjects=true",
            ]
        )
    command.extend(args)
    result = bounded_process_output(
        command,
        safe_process_environment(extra_environment),
        timeout=timeout,
        max_bytes=file_bytes,
    )
    if result is None:
        raise DeltaError(f"{description} timed out or exceeded bounded output")
    status, output = result
    if len(output) > max_bytes:
        raise DeltaError(f"{description} timed out or exceeded bounded output")
    if status not in allowed_statuses:
        raise DeltaError(f"{description} failed closed (exit {status})")
    return status, output


def git_binary_output(
    git_root: Path,
    *args: str,
    description: str = "Git command",
    timeout: int = GIT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_GIT_OUTPUT_BYTES,
    max_file_bytes: int | None = None,
    remote: bool = False,
    extra_environment: dict[str, str] | None = None,
) -> bytes:
    return git_process(
        git_root,
        *args,
        description=description,
        timeout=timeout,
        max_bytes=max_bytes,
        max_file_bytes=max_file_bytes,
        remote=remote,
        extra_environment=extra_environment,
    )[1]


def git_stdout(
    repo: Path,
    *args: str,
    description: str,
    extra_environment: dict[str, str] | None = None,
) -> str:
    output = git_binary_output(
        repo,
        *args,
        description=description,
        extra_environment=extra_environment,
    )
    try:
        return output.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise DeltaError(f"{description} returned non-UTF-8 output") from exc


def expected_oid_length(object_format: str) -> int:
    if object_format == "sha1":
        return 40
    if object_format == "sha256":
        return 64
    raise DeltaError(f"unsupported Git object format: {object_format}")


def parse_single_oid(value: str | bytes, description: str, object_format: str) -> str:
    try:
        text = value.decode("ascii") if isinstance(value, bytes) else value
    except UnicodeDecodeError as exc:
        raise DeltaError(f"{description} returned an invalid object ID") from exc
    lines = text.splitlines()
    if (
        len(lines) != 1
        or not OID_RE.fullmatch(lines[0])
        or len(lines[0]) != expected_oid_length(object_format)
    ):
        raise DeltaError(f"{description} returned an ambiguous or invalid object ID")
    return lines[0]


def normalize_expected_origin(value: str) -> tuple[str, tuple[str, str]]:
    identity = canonical_repository(value)
    if identity is None or identity[0] != TRUSTED_GIT_HOST:
        raise DeltaError(
            "--expected-origin must be one canonical credential-free "
            "gitlab.addx.ai repository"
        )
    host, project = identity
    canonical_forms = {
        f"https://{host}/{project}.git",
        f"ssh://git@{host}/{project}.git",
        f"git@{host}:{project}.git",
    }
    if value not in canonical_forms:
        raise DeltaError(
            "--expected-origin must use canonical HTTPS or git SSH syntax ending in .git"
        )
    return value, identity


def verified_remote_transport(
    local_origin: str,
    expected_identity: tuple[str, str],
) -> str:
    """Select a no-helper transport for an already proven repository identity."""
    if canonical_repository(local_origin) != expected_identity:
        raise DeltaError("candidate repository transport identity is inconsistent")
    if local_origin.startswith("git@") or local_origin.startswith("ssh://git@"):
        return local_origin
    host, project = expected_identity
    return f"git@{host}:{project}.git"


def resolve_repo_and_relative_scope(directory: Path) -> tuple[Path, Path]:
    try:
        resolved_directory = directory.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DeltaError(f"validation directory does not exist: {directory}") from exc
    if not resolved_directory.is_dir():
        raise DeltaError(f"validation directory does not exist: {resolved_directory}")
    repo_output = git_stdout(
        resolved_directory,
        "rev-parse",
        "--show-toplevel",
        description="cannot resolve validation Git worktree",
    )
    try:
        repo = Path(repo_output).resolve(strict=True)
        relative_scope = resolved_directory.relative_to(repo)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DeltaError(
            f"validation directory escapes its Git worktree: {resolved_directory}"
        ) from exc
    return repo, relative_scope


def resolve_trusted_base_ref(_repo: Path, supplied: str | None) -> str:
    if supplied is None or not supplied.startswith("origin/"):
        raise DeltaError(
            "--base-ref must be an origin remote-tracking branch such as origin/main; "
            "local branches, commit hashes, and arbitrary refs are not trusted"
        )
    branch = supplied.removeprefix("origin/")
    if (
        branch in {"", "HEAD"}
        or not BRANCH_RE.fullmatch(branch)
        or branch.startswith("/")
        or branch.endswith(("/", "."))
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or any(part.startswith(".") or part.endswith(".lock") for part in branch.split("/"))
    ):
        raise DeltaError(
            "--base-ref must resolve to a concrete origin remote-tracking branch, "
            "not origin/HEAD or an invalid ref"
        )
    return branch


def normalized_platform_source(repo: Path, source: Path | None) -> Path | None:
    if source is None:
        return None
    candidate = source if source.is_absolute() else repo / source
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(repo)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DeltaError(
            "--platform-source must be inside the same Git worktree as the "
            "validation directory"
        ) from exc
    if not resolved.is_dir():
        raise DeltaError("--platform-source must be an existing directory")
    return relative


def caller_object_context(repo: Path) -> tuple[Path, str]:
    object_format = git_stdout(
        repo,
        "rev-parse",
        "--show-object-format",
        description="cannot resolve candidate Git object format",
    )
    expected_oid_length(object_format)
    object_path = Path(
        git_stdout(
            repo,
            "rev-parse",
            "--git-path",
            "objects",
            description="cannot resolve candidate Git object store",
        )
    )
    if not object_path.is_absolute():
        object_path = repo / object_path
    try:
        resolved_objects = object_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DeltaError("candidate Git object store is unavailable") from exc
    if not resolved_objects.is_dir():
        raise DeltaError("candidate Git object store is unavailable")
    return resolved_objects, object_format


def safe_alternate_path(path: Path) -> str:
    value = str(path.resolve())
    if any(marker in value for marker in ("\x00", "\n", "\r")):
        raise DeltaError("Git object store path is not representable safely")
    return value


def write_alternates(objects_root: Path, stores: list[Path]) -> None:
    info = objects_root / "info"
    if not objects_root.is_dir():
        raise DeltaError("temporary Git object store was not initialized")
    info.mkdir(parents=True, exist_ok=True)
    values: list[str] = []
    for store in stores:
        value = safe_alternate_path(store)
        if value not in values:
            values.append(value)
    try:
        (info / "alternates").write_text(
            "".join(f"{value}\n" for value in values),
            encoding="utf-8",
        )
    except OSError as exc:
        raise DeltaError("cannot bind temporary Git object stores") from exc


@contextmanager
def fresh_remote_proof(
    caller_repo: Path,
    caller_objects: Path,
    object_format: str,
    remote_transport: str,
    target_branch: str,
) -> Iterator[RemoteProof]:
    del caller_repo
    with tempfile.TemporaryDirectory(prefix="cicd-validator-remote-proof-") as temp:
        temp_root = Path(temp)
        proof_root = temp_root / "proof.git"
        empty_template = temp_root / "empty-template"
        empty_template.mkdir()
        git_binary_output(
            temp_root,
            "init",
            "--quiet",
            "--bare",
            f"--object-format={object_format}",
            f"--template={empty_template}",
            str(proof_root),
            description="cannot initialize fresh remote proof repository",
        )
        write_alternates(proof_root / "objects", [caller_objects])
        git_binary_output(
            proof_root,
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-recurse-submodules",
            "--filter=blob:none",
            remote_transport,
            f"+refs/heads/{target_branch}:refs/remotes/proof/target",
            description="fresh remote target fetch",
            timeout=REMOTE_GIT_TIMEOUT_SECONDS,
            max_bytes=MAX_REMOTE_GIT_OUTPUT_BYTES,
            remote=True,
        )
        target_output = git_binary_output(
            proof_root,
            "rev-parse",
            "--verify",
            "refs/remotes/proof/target^{commit}",
            description="fresh remote target resolution",
        )
        target_commit = parse_single_oid(
            target_output,
            "fresh remote target resolution",
            object_format,
        )
        yield RemoteProof(proof_root, target_commit, object_format)


def require_clean_candidate(repo: Path, candidate_commit: str) -> None:
    with tempfile.TemporaryDirectory(prefix="cicd-validator-clean-index-") as temp:
        index = Path(temp) / "index"
        index_environment = {"GIT_INDEX_FILE": str(index)}
        git_binary_output(
            repo,
            "read-tree",
            candidate_commit,
            description="cannot build controlled candidate index",
            max_file_bytes=MAX_GIT_INDEX_BYTES,
            extra_environment=index_environment,
        )
        refresh_status, _refresh_output = git_process(
            repo,
            "update-index",
            "--refresh",
            "--ignore-submodules",
            description="cannot refresh controlled candidate index",
            max_file_bytes=MAX_GIT_INDEX_BYTES,
            extra_environment=index_environment,
            allowed_statuses=frozenset({0, 1}),
        )
        status, _output = git_process(
            repo,
            "diff-files",
            "--quiet",
            "--ignore-submodules=none",
            "--",
            description="cannot inspect candidate tracked files",
            extra_environment=index_environment,
            allowed_statuses=frozenset({0, 1}),
        )
        untracked = git_binary_output(
            repo,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            description="cannot inspect candidate untracked files",
            extra_environment=index_environment,
        )
    if refresh_status != 0 or status != 0 or untracked:
        raise DeltaError(
            "candidate worktree is not clean; commit or stash every change before "
            "running validator delta so the checked snapshot is reproducible"
        )


def merge_base_from_proof(
    proof: RemoteProof,
    candidate_commit: str,
) -> str:
    output = git_stdout(
        proof.root,
        "merge-base",
        "--all",
        candidate_commit,
        proof.target_commit,
        description="cannot compute merge-base with fresh remote target",
    )
    return parse_single_oid(
        output,
        "fresh remote target merge-base",
        proof.object_format,
    )


def changed_scope_paths(
    proof: RemoteProof,
    base_commit: str,
    candidate_commit: str,
    relative_scope: Path,
) -> set[str]:
    """Return the candidate write set as paths relative to the validated scope."""
    args = [
        "--literal-pathspecs",
        "diff",
        "--name-only",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=none",
        "-z",
        base_commit,
        candidate_commit,
        "--",
    ]
    if relative_scope.parts:
        args.append(relative_scope.as_posix())
    output = git_binary_output(
        proof.root,
        *args,
        description="cannot compute candidate write set within validated scope",
        max_bytes=MAX_TREE_OUTPUT_BYTES,
    )
    if output and not output.endswith(b"\x00"):
        raise DeltaError("candidate write-set output is incomplete")

    changed: set[str] = set()
    scope_parts = (
        PurePosixPath(relative_scope.as_posix()).parts
        if relative_scope.parts
        else ()
    )
    for raw_path in output.rstrip(b"\x00").split(b"\x00") if output else ():
        path = canonical_tree_path(raw_path, relative_scope, "candidate write set")
        relative_parts = path.parts[len(scope_parts) :]
        if not relative_parts:
            raise DeltaError("candidate write set contains an ambiguous scope-root path")
        changed.add(PurePosixPath(*relative_parts).as_posix())
        if len(changed) > MAX_SNAPSHOT_FILES:
            raise DeltaError("candidate write set exceeds the path-count budget")
    return changed


def canonical_tree_path(raw_path: bytes, requested: Path, label: str) -> PurePosixPath:
    try:
        value = raw_path.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeltaError(f"{label} snapshot contains a non-UTF-8 Git path") from exc
    path = PurePosixPath(value)
    parts = path.parts
    requested_parts = PurePosixPath(requested.as_posix()).parts if requested.parts else ()
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(part in {"", ".", ".."} or part.casefold() == ".git" for part in parts)
        or (requested_parts and parts[: len(requested_parts)] != requested_parts)
    ):
        raise DeltaError(f"{label} snapshot contains an unsafe Git path")
    return path


def parse_tree_entries(
    output: bytes,
    requested: Path,
    label: str,
    object_format: str,
) -> list[TreeEntry]:
    if output and not output.endswith(b"\x00"):
        raise DeltaError(f"{label} snapshot tree output is incomplete")
    entries: list[TreeEntry] = []
    for record in output.rstrip(b"\x00").split(b"\x00") if output else ():
        try:
            header, raw_path = record.split(b"\t", 1)
            raw_mode, raw_type, raw_oid = header.split(b" ", 2)
            mode = raw_mode.decode("ascii")
            object_type = raw_type.decode("ascii")
            oid = raw_oid.decode("ascii")
        except ValueError as exc:
            raise DeltaError(f"{label} snapshot tree output is malformed") from exc
        path = canonical_tree_path(raw_path, requested, label)
        if mode not in {"100644", "100755"} or object_type != "blob":
            raise DeltaError(
                f"{label} snapshot scope contains a symlink, gitlink, or unsupported mode"
            )
        if not OID_RE.fullmatch(oid) or len(oid) != expected_oid_length(object_format):
            raise DeltaError(f"{label} snapshot tree contains an invalid blob ID")
        entries.append(TreeEntry(mode, oid, path))
    if not entries:
        display = requested.as_posix() if requested.parts else "."
        raise DeltaError(f"{label} snapshot does not contain requested path: {display}")
    return entries


def requested_tree_entries(
    proof: RemoteProof,
    revision: str,
    requested: Path,
    label: str,
) -> list[TreeEntry]:
    args = ["--literal-pathspecs", "ls-tree", "-r", "-z", "--full-tree", revision]
    if requested.parts:
        args.extend(["--", requested.as_posix()])
    output = git_binary_output(
        proof.root,
        *args,
        description=f"cannot enumerate {label} snapshot path",
        max_bytes=MAX_TREE_OUTPUT_BYTES,
    )
    return parse_tree_entries(output, requested, label, proof.object_format)


def snapshot_entries(
    proof: RemoteProof,
    revision: str,
    requested_paths: list[Path],
    label: str,
) -> list[TreeEntry]:
    by_path: dict[PurePosixPath, TreeEntry] = {}
    for requested in requested_paths:
        for entry in requested_tree_entries(proof, revision, requested, label):
            existing = by_path.get(entry.path)
            if existing is not None and existing != entry:
                raise DeltaError(f"{label} snapshot has a conflicting Git path")
            by_path[entry.path] = entry
            if len(by_path) > MAX_SNAPSHOT_FILES:
                raise DeltaError(f"{label} snapshot exceeds the file-count budget")
    return sorted(by_path.values(), key=lambda entry: entry.path.as_posix())


def configure_snapshot_repository(snapshot: Path, expected_origin: str) -> None:
    for key, value in (
        ("core.fsmonitor", "false"),
        ("core.hooksPath", os.devnull),
        ("remote.origin.url", expected_origin),
    ):
        git_binary_output(
            snapshot,
            "config",
            "--local",
            "--replace-all",
            key,
            value,
            description="cannot configure inert validation snapshot",
        )


def materialize_snapshot(
    destination: Path,
    proof: RemoteProof,
    caller_objects: Path,
    revision: str,
    expected_origin: str,
    requested_paths: list[Path],
    label: str,
) -> None:
    empty_template = destination.parent / f"{destination.name}-empty-template"
    empty_template.mkdir()
    git_binary_output(
        destination.parent,
        "init",
        "--quiet",
        f"--object-format={proof.object_format}",
        f"--template={empty_template}",
        str(destination),
        description=f"cannot initialize {label} validation snapshot",
    )
    write_alternates(
        destination / ".git" / "objects",
        [caller_objects, proof.root / "objects"],
    )
    configure_snapshot_repository(destination, expected_origin)
    git_binary_output(
        destination,
        "read-tree",
        revision,
        description=f"cannot build {label} validation snapshot index",
        max_file_bytes=MAX_GIT_INDEX_BYTES,
    )
    git_binary_output(
        destination,
        "update-ref",
        "--no-deref",
        "HEAD",
        revision,
        description=f"cannot pin {label} validation snapshot HEAD",
    )
    entries = snapshot_entries(proof, revision, requested_paths, label)
    total_bytes = 0
    for entry in entries:
        size_text = git_stdout(
            proof.root,
            "cat-file",
            "-s",
            entry.oid,
            description=f"cannot inspect {label} snapshot blob",
        )
        try:
            size = int(size_text)
        except ValueError as exc:
            raise DeltaError(f"{label} snapshot blob has an invalid size") from exc
        if size < 0 or size > MAX_SNAPSHOT_BLOB_BYTES:
            raise DeltaError(f"{label} snapshot blob exceeds the per-file budget")
        total_bytes += size
        if total_bytes > MAX_SNAPSHOT_TOTAL_BYTES:
            raise DeltaError(f"{label} snapshot exceeds the total-byte budget")
        blob = git_binary_output(
            proof.root,
            "cat-file",
            "blob",
            entry.oid,
            description=f"cannot read {label} snapshot blob",
            max_bytes=max(1, size),
        )
        if len(blob) != size:
            raise DeltaError(f"{label} snapshot blob length is inconsistent")
        target = destination.joinpath(*entry.path.parts)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(blob)
            target.chmod(0o644)
        except OSError as exc:
            raise DeltaError(f"cannot materialize {label} validation snapshot") from exc


def strict_command(
    scope: Path,
    repo_context: str,
    platform_source: Path | None,
    objectbucket_target: str | None,
) -> list[str]:
    command = ["bash", str(STRICT_VALIDATOR), "--repo-context", repo_context]
    if platform_source is not None:
        command.extend(["--platform-source", str(platform_source)])
    if objectbucket_target is not None:
        command.extend(["--objectbucket-target", objectbucket_target])
    command.append(str(scope))
    return command


def is_summary_line(line: str) -> bool:
    return any(summary_re.fullmatch(line) for summary_re in SUMMARY_RES)


def normalize_vault_breadcrumb_indices(reason: str) -> str:
    """Normalize only list positions in known ``check_vault_paths`` fields."""

    def normalize_match(match: re.Match[str]) -> str:
        return (
            f"{match.groupdict().get('prefix', '')}"
            f"{LIST_INDEX_RE.sub('[*]', match.group('breadcrumb'))}"
            f"{match.group('suffix')}"
        )

    reason = VAULT_BREADCRUMB_PREFIX_RE.sub(normalize_match, reason, count=1)
    return VAULT_BREADCRUMB_NO_RULE_RE.sub(normalize_match, reason, count=1)


def normalize_findings(output: str, scope: Path, label: str) -> Counter[Finding]:
    """Turn strict ``FAIL:`` records into stable base/candidate fingerprints."""
    current_validator: str | None = None
    findings: Counter[Finding] = Counter()
    scope_texts = [str(scope)]
    resolved_scope_text = str(scope.resolve())
    if resolved_scope_text not in scope_texts:
        scope_texts.append(resolved_scope_text)
    prefixes = tuple(f"FAIL: {value}" for value in scope_texts)

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        header = HEADER_RE.match(line)
        if header:
            current_validator = header.group("validator")
            continue
        if not line.startswith("FAIL: ") or is_summary_line(line):
            continue
        if current_validator is None:
            raise DeltaError(
                f"{label} strict validation emitted a FAIL record before a validator "
                f"header; refusing to hide it: {line}"
            )
        prefix = next((value for value in prefixes if line.startswith(value)), None)
        if prefix is None:
            raise DeltaError(
                f"{label} strict validation emitted an unrecognized FAIL record; "
                f"refusing to hide it: {line}"
            )
        remainder = line[len(prefix) :]
        if remainder.startswith("/"):
            remainder = remainder[1:]
        elif remainder.startswith(": "):
            remainder = f".: {remainder[2:]}"
        else:
            raise DeltaError(
                f"{label} strict validation emitted an ambiguous path record; "
                f"refusing to hide it: {line}"
            )
        if ": " not in remainder:
            raise DeltaError(
                f"{label} strict validation emitted an incomplete FAIL record; "
                f"refusing to hide it: {line}"
            )
        relative_path, detail = remainder.split(": ", 1)
        if not relative_path or relative_path.startswith("../"):
            raise DeltaError(
                f"{label} strict validation emitted an unsafe relative path; "
                f"refusing to hide it: {line}"
            )
        object_match = OBJECT_RE.match(detail)
        if object_match:
            obj = object_match.group("object")
            reason = object_match.group("reason")
        else:
            obj = "<path>"
            reason = detail
        for scope_text in scope_texts:
            reason = reason.replace(scope_text, ".")
        if current_validator == "check_vault_paths.py":
            reason = normalize_vault_breadcrumb_indices(reason)
        finding = Finding(
            validator=current_validator,
            relative_path=relative_path,
            obj=obj,
            reason=reason,
        )
        findings[finding] += 1
    return findings


def bounded_error_detail(output: str) -> str:
    encoded = output.strip().encode("utf-8", errors="replace")
    if len(encoded) <= MAX_STRICT_ERROR_DETAIL_BYTES:
        return encoded.decode("utf-8", errors="replace")
    return encoded[:MAX_STRICT_ERROR_DETAIL_BYTES].decode("utf-8", errors="ignore") + "...[truncated]"


def run_strict_suite(
    label: str,
    scope: Path,
    repo_context: str,
    platform_source: Path | None,
    objectbucket_target: str | None,
) -> StrictResult:
    bash = shutil.which("bash", path=os.defpath)
    if bash is None:
        raise DeltaError("bash dependency is unavailable")
    command = strict_command(scope, repo_context, platform_source, objectbucket_target)
    wrapped = [
        str(Path(bash).resolve()),
        "-c",
        'exec "$@" 2>&1',
        "cicd-validator-strict",
        *command,
    ]
    with strict_process_environment(Path(bash).resolve()) as environment:
        result = bounded_process_output(
            wrapped,
            environment,
            timeout=STRICT_TIMEOUT_SECONDS,
            max_bytes=MAX_STRICT_OUTPUT_BYTES,
        )
    if result is None:
        raise DeltaError(
            f"{label} strict validation timed out or exceeded bounded output; "
            "delta comparison fails closed"
        )
    status, raw_output = result
    try:
        output = raw_output.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeltaError(
            f"{label} strict validation emitted non-UTF-8 output; "
            "delta comparison fails closed"
        ) from exc
    if status not in (0, 1):
        detail = bounded_error_detail(output)
        suffix = f"\n{detail}" if detail else ""
        raise DeltaError(
            f"{label} strict validation was unavailable or received invalid input "
            f"(exit {status}); delta comparison fails closed{suffix}"
        )
    findings = normalize_findings(output, scope, label)
    if status == 1 and not findings:
        raise DeltaError(
            f"{label} strict validation exited with policy failure but emitted no "
            "normalized findings; delta comparison fails closed"
        )
    return StrictResult(status=status, findings=findings)


def print_finding_details(
    reporter: BoundedReporter,
    title: str,
    findings: Counter[Finding],
) -> None:
    values = sorted(findings)
    if not values:
        return
    reporter.line(f"{title} DETAILS:")
    for finding in values:
        count = findings[finding]
        duplicate_suffix = f" (x{count})" if count > 1 else ""
        reporter.line(f"  - {finding.render()}{duplicate_suffix}")


def print_path_details(
    reporter: BoundedReporter,
    title: str,
    paths: set[str],
) -> None:
    if not paths:
        return
    reporter.line(f"{title} DETAILS:")
    for path in sorted(paths):
        reporter.line(f"  - {path}")


def report_delta_comparison(
    reporter: BoundedReporter,
    *,
    expected_origin: str,
    target_branch: str,
    remote_target_commit: str,
    base_commit: str,
    candidate_commit: str,
    base_result: StrictResult,
    candidate_result: StrictResult,
    candidate_write_set: set[str],
) -> int:
    baseline = base_result.findings
    candidate = candidate_result.findings
    carried = baseline & candidate
    base_only = baseline - candidate
    introduced = candidate - baseline
    carried_on_changed_paths = Counter(
        {
            finding: count
            for finding, count in carried.items()
            if finding.relative_path in candidate_write_set
        }
    )
    finding_groups = (
        ("BASELINE DEBT (present at merge-base)", baseline),
        ("CARRIED BASELINE DEBT", carried),
        ("BASELINE-ONLY DEBT (resolved by candidate)", base_only),
        ("CANDIDATE-ONLY POLICY FINDINGS", introduced),
    )
    changed_title = "CANDIDATE WRITE SET (within validated scope)"
    overlap_title = "CARRIED BASELINE DEBT ON CANDIDATE-CHANGED PATHS"

    reporter.line("validator delta comparison")
    reporter.line(f"  expected origin: {expected_origin}")
    reporter.line(
        f"  exact remote target: origin/{target_branch} ({remote_target_commit})"
    )
    reporter.line(f"  trusted merge-base: {base_commit}")
    reporter.line(f"  candidate: {candidate_commit}")
    reporter.line(
        "  strict suite status: "
        f"base={base_result.status}, candidate={candidate_result.status}"
    )
    for title, findings in finding_groups:
        reporter.line(f"{title}: {sum(findings.values())}")
    reporter.line(f"{changed_title}: {len(candidate_write_set)}")
    reporter.line(f"{overlap_title}: {sum(carried_on_changed_paths.values())}")

    if introduced:
        reporter.line("FAIL: validator delta found candidate-only policy findings")
    if carried_on_changed_paths:
        reporter.line(
            "FAIL: carried baseline debt overlaps the candidate write set; "
            "overlapping debt must be fixed"
        )
    status = 1 if introduced or carried_on_changed_paths else 0
    if status == 0:
        reporter.line(
            "PASS: validator delta found no candidate-only policy findings and no "
            "carried baseline debt overlaps the candidate write set"
        )

    for title, findings in finding_groups:
        print_finding_details(reporter, title, findings)
    print_path_details(reporter, changed_title, candidate_write_set)
    print_finding_details(reporter, overlap_title, carried_on_changed_paths)
    return status


def prepare_candidate(args: argparse.Namespace) -> CandidateContext:
    expected_origin, expected_identity = normalize_expected_origin(args.expected_origin)
    target_branch = resolve_trusted_base_ref(Path("."), args.base_ref)
    repo, relative_scope = resolve_repo_and_relative_scope(args.directory)
    local_origin = repository_origin(repo)
    if local_origin is None:
        raise DeltaError(
            "candidate repository origin cannot be proven from one bounded inert "
            "local config"
        )
    if (
        canonical_repository(local_origin) != expected_identity
        or not same_repository(local_origin, expected_origin)
    ):
        raise DeltaError("candidate repository origin does not match --expected-origin")
    remote_transport = verified_remote_transport(local_origin, expected_identity)
    caller_objects, object_format = caller_object_context(repo)
    candidate_commit = parse_single_oid(
        git_stdout(
            repo,
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
            description="cannot resolve committed candidate HEAD",
        ),
        "candidate HEAD",
        object_format,
    )
    require_clean_candidate(repo, candidate_commit)
    platform_source = normalized_platform_source(repo, args.platform_source)
    requested_paths = [relative_scope]
    if platform_source is not None and platform_source not in requested_paths:
        requested_paths.append(platform_source)
    return CandidateContext(
        expected_origin=expected_origin,
        target_branch=target_branch,
        repo=repo,
        relative_scope=relative_scope,
        caller_objects=caller_objects,
        object_format=object_format,
        candidate_commit=candidate_commit,
        remote_transport=remote_transport,
        platform_source=platform_source,
        requested_paths=tuple(requested_paths),
    )


def proven_commits(
    proof: RemoteProof,
    context: CandidateContext,
) -> tuple[str, str]:
    if proof.object_format != context.object_format:
        raise DeltaError("fresh remote proof uses a different Git object format")
    remote_target_commit = parse_single_oid(
        proof.target_commit,
        "fresh remote target",
        context.object_format,
    )
    base_commit = merge_base_from_proof(proof, context.candidate_commit)
    if base_commit != remote_target_commit:
        raise DeltaError(
            "fresh remote target is not an ancestor of candidate HEAD; "
            "rebase or rebuild the candidate and rerun against the exact fresh target"
        )
    if base_commit == context.candidate_commit:
        raise DeltaError(
            "trusted merge-base equals candidate HEAD; run strict validate.sh "
            "for an unchanged/behind branch instead of accepting a zero-diff baseline"
        )
    return remote_target_commit, base_commit


def snapshot_scope(root: Path, relative_scope: Path, label: str) -> Path:
    scope = root / relative_scope
    if scope.is_dir():
        return scope
    if label == "base":
        raise DeltaError(
            "the validation directory does not exist at the trusted "
            f"merge-base: {relative_scope or Path('.')}; choose a tracked "
            "common scope instead"
        )
    raise DeltaError(
        "the validation directory does not exist at candidate HEAD; "
        "candidate snapshot is invalid"
    )


def snapshot_platform_source(
    root: Path,
    relative: Path | None,
) -> Path | None:
    if relative is None:
        return None
    source = root / relative
    if not source.is_dir():
        raise DeltaError(
            "--platform-source must exist in both base and candidate snapshots; "
            "delta comparison cannot infer a new platform source"
        )
    return source


def materialized_strict_results(
    proof: RemoteProof,
    context: CandidateContext,
    base_commit: str,
    args: argparse.Namespace,
) -> tuple[StrictResult, StrictResult]:
    with tempfile.TemporaryDirectory(prefix="cicd-validator-delta-") as temp:
        temp_root = Path(temp)
        base_root = temp_root / "base"
        candidate_root = temp_root / "candidate"
        materialize_snapshot(
            base_root,
            proof,
            context.caller_objects,
            base_commit,
            context.expected_origin,
            list(context.requested_paths),
            "base",
        )
        materialize_snapshot(
            candidate_root,
            proof,
            context.caller_objects,
            context.candidate_commit,
            context.expected_origin,
            list(context.requested_paths),
            "candidate",
        )
        base_result = run_strict_suite(
            "base",
            snapshot_scope(base_root, context.relative_scope, "base"),
            args.repo_context,
            snapshot_platform_source(base_root, context.platform_source),
            args.objectbucket_target,
        )
        candidate_result = run_strict_suite(
            "candidate",
            snapshot_scope(candidate_root, context.relative_scope, "candidate"),
            args.repo_context,
            snapshot_platform_source(candidate_root, context.platform_source),
            args.objectbucket_target,
        )
    return base_result, candidate_result


def compare_fresh_target(
    context: CandidateContext,
    args: argparse.Namespace,
    remote_proof_factory: RemoteProofFactory,
) -> ComparisonEvidence:
    with remote_proof_factory(
        context.repo,
        context.caller_objects,
        context.object_format,
        context.remote_transport,
        context.target_branch,
    ) as proof:
        remote_target_commit, base_commit = proven_commits(proof, context)
        candidate_write_set = changed_scope_paths(
            proof,
            base_commit,
            context.candidate_commit,
            context.relative_scope,
        )
        base_result, candidate_result = materialized_strict_results(
            proof,
            context,
            base_commit,
            args,
        )
    return ComparisonEvidence(
        remote_target_commit=remote_target_commit,
        base_commit=base_commit,
        base_result=base_result,
        candidate_result=candidate_result,
        candidate_write_set=candidate_write_set,
    )


def recheck_candidate(context: CandidateContext) -> None:
    final_candidate = parse_single_oid(
        git_stdout(
            context.repo,
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
            description="cannot re-read committed candidate HEAD",
        ),
        "candidate HEAD re-check",
        context.object_format,
    )
    if final_candidate != context.candidate_commit:
        raise DeltaError(
            "candidate HEAD changed during validation; rerun against the new candidate SHA"
        )
    require_clean_candidate(context.repo, context.candidate_commit)


def invalid_cli_configuration(
    args: argparse.Namespace,
    reporter: BoundedReporter,
) -> int | None:
    if not STRICT_VALIDATOR.is_file():
        reporter.line(f"FAIL: strict validator is missing: {STRICT_VALIDATOR}", stderr=True)
        return 2
    if args.platform_source is not None and args.repo_context != "k8s":
        reporter.line(
            "FAIL: --platform-source is valid only with --repo-context k8s",
            stderr=True,
        )
        return 2
    return None


def main(
    argv: list[str],
    *,
    remote_proof_factory: RemoteProofFactory = fresh_remote_proof,
) -> int:
    reporter = BoundedReporter()
    try:
        args = parse_args(argv, reporter)
    except ArgumentParseExit as exc:
        return exc.status
    invalid_status = invalid_cli_configuration(args, reporter)
    if invalid_status is not None:
        return invalid_status

    try:
        context = prepare_candidate(args)
        evidence = compare_fresh_target(context, args, remote_proof_factory)
        recheck_candidate(context)
        return report_delta_comparison(
            reporter,
            expected_origin=context.expected_origin,
            target_branch=context.target_branch,
            remote_target_commit=evidence.remote_target_commit,
            base_commit=evidence.base_commit,
            candidate_commit=context.candidate_commit,
            base_result=evidence.base_result,
            candidate_result=evidence.candidate_result,
            candidate_write_set=evidence.candidate_write_set,
        )
    except DeltaError as exc:
        reporter.line(f"FAIL: {exc}", stderr=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
