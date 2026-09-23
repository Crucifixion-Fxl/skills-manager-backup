#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Provision one scoped GitLab project token through the user's local glab session.

The one-time token value is captured in process memory, written only to the
target Agent's 0600 env file, and never printed. Caught failures restore this
operation's env write and revoke its token; abrupt termination leaves a
non-secret pending journal for exact reconciliation with local glab.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


class ProvisionError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class TokenProfile:
    access_level: int
    scopes: tuple[str, ...]
    # Agents that push branches and open MRs trigger MR pipelines as the bot, and the instance's CI config lives in an
    # Internal project (engineering/ci-templates) that an external user cannot read: the pipeline is created failed
    # with no jobs. GitLab also refuses to add a project bot to another project, so the only bot-side fix is to keep
    # such bots non-external. They can then read every Internal project (read-only); Planner/Reporter keep external.
    external_default: bool = True


PROFILES = {
    "planner": TokenProfile(15, ("api",)),
    "reporter": TokenProfile(20, ("api", "read_repository")),
    "developer": TokenProfile(30, ("api", "write_repository"), external_default=False),
}
EXTERNAL_CHOICES = ("auto", "yes", "no")


def resolve_external(profile: TokenProfile, choice: Any) -> bool:
    if choice == "auto":
        return profile.external_default
    if choice in ("yes", "no"):
        return choice == "yes"
    raise ProvisionError(f"external must be one of {', '.join(EXTERNAL_CHOICES)}")


TOKEN_LINE = re.compile(r"^(?:export[ \t]+)?#?[ \t]*GITLAB_TOKEN=(.*)$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SAFE_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
AGENT_RUNTIME_MARKERS = {
    "BUZZ_AGENT_NAME", "BUZZ_AGENT_PUBKEY", "BUZZ_AUTH_TAG", "BUZZ_ACP_CHANNELS",
}


def _json_object(raw: str, context: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProvisionError(f"{context} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProvisionError(f"{context} did not return a JSON object")
    return value


def _validated_glab_path(explicit: str | None) -> str:
    candidate = explicit or shutil.which("glab")
    if not candidate:
        raise ProvisionError("glab is not installed or not on PATH")
    path = Path(candidate).expanduser().resolve(strict=True)
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        raise ProvisionError("glab must resolve to a regular executable")
    if metadata.st_uid not in {0, os.getuid()} or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ProvisionError("glab must be owned by root/current user and not group/world writable")
    return str(path)


class GlabClient:
    def __init__(
        self,
        host: str,
        *,
        glab_path: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if not SAFE_HOST.fullmatch(host) or "://" in host or "/" in host:
            raise ProvisionError("host must be a bare DNS name")
        self.host = host.lower()
        self.glab_path = _validated_glab_path(glab_path)
        self.runner = runner
        self.env = os.environ.copy()
        for key in (
            "GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN", "PRIVATE_TOKEN",
            "JOB_TOKEN", "CI_JOB_TOKEN", "GITLAB_HOST", "GITLAB_API_HOST",
            "GITLAB_URI", "GITLAB_URL",
        ):
            self.env.pop(key, None)

    def request(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        command = [self.glab_path, "api", "--hostname", self.host, "--method", method]
        input_text = None
        if payload is not None:
            command.extend(["--header", "Content-Type: application/json", "--input", "-"])
            input_text = json.dumps(payload, separators=(",", ":"))
        command.append(endpoint)
        try:
            completed = self.runner(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
                env=self.env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProvisionError(f"glab {method} {endpoint} could not run") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            suffix = f": {detail[-1][:300]}" if detail else ""
            raise ProvisionError(f"glab {method} {endpoint} failed{suffix}")
        if allow_empty and not completed.stdout.strip():
            return {}
        return _json_object(completed.stdout, f"glab {method} {endpoint}")

    def request_list(self, endpoint: str) -> list[dict[str, Any]]:
        command = [self.glab_path, "api", "--hostname", self.host, "--method", "GET", endpoint]
        try:
            completed = self.runner(
                command,
                input=None,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
                env=self.env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProvisionError(f"glab GET {endpoint} could not run") from exc
        if completed.returncode != 0:
            raise ProvisionError(f"glab GET {endpoint} failed")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ProvisionError(f"glab GET {endpoint} returned invalid JSON") from exc
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ProvisionError(f"glab GET {endpoint} did not return an object list")
        return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class AgentTokenClient:
    def __init__(self, host: str, token: str) -> None:
        self.base = f"https://{host}/api/v4"
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _page(self, endpoint: str, params: dict[str, str] | None = None) -> tuple[Any, str]:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base}{endpoint}" + (f"?{query}" if query else "")
        request = urllib.request.Request(
            url,
            headers={"PRIVATE-TOKEN": self.token, "Accept": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                next_page = response.headers.get("X-Next-Page", "")
        except urllib.error.HTTPError as exc:
            raise ProvisionError(f"new token GET {endpoint} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ProvisionError(f"new token GET {endpoint} failed") from exc
        try:
            return json.loads(raw), next_page
        except json.JSONDecodeError as exc:
            raise ProvisionError(f"new token GET {endpoint} returned invalid JSON") from exc

    def get_object(self, endpoint: str) -> dict[str, Any]:
        value, _ = self._page(endpoint)
        if not isinstance(value, dict):
            raise ProvisionError(f"new token GET {endpoint} did not return an object")
        return value

    def get_all(self, endpoint: str, params: dict[str, str]) -> list[dict[str, Any]]:
        page = 1
        items: list[dict[str, Any]] = []
        while True:
            value, next_page = self._page(endpoint, {**params, "per_page": "100", "page": str(page)})
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ProvisionError(f"new token GET {endpoint} did not return an object list")
            items.extend(value)
            if not next_page:
                return items
            if not next_page.isdigit() or int(next_page) <= page or page >= 100:
                raise ProvisionError(f"new token GET {endpoint} returned invalid pagination")
            page = int(next_page)


def _read_secure_env(path: Path) -> tuple[str, os.stat_result]:
    if not path.is_absolute():
        raise ProvisionError("env file must be an absolute existing readable non-symlink")
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(fd)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = None
            content = stream.read()
    except (OSError, UnicodeError) as exc:
        raise ProvisionError("env file must be an absolute existing readable non-symlink") from exc
    finally:
        if fd is not None:
            os.close(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ProvisionError("env file must be a regular file owned by the current user")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ProvisionError("env file mode must be exactly 0600")
    return content, metadata


def _read_env_placeholder(path: Path) -> tuple[str, int]:
    content, _ = _read_secure_env(path)
    matches: list[tuple[int, str]] = []
    for index, line in enumerate(content.splitlines(keepends=True)):
        match = TOKEN_LINE.fullmatch(line.rstrip("\r\n"))
        if match:
            matches.append((index, match.group(1)))
    if len(matches) != 1:
        raise ProvisionError("env file must contain exactly one GITLAB_TOKEN placeholder")
    index, value = matches[0]
    if value.strip() and value.strip() not in {"<pending>", "<authorized-admin-must-issue>"}:
        raise ProvisionError("refusing to overwrite an existing GITLAB_TOKEN")
    return content, index


def _atomic_replace(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = None
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _render_injected_env(original: str, line_index: int, token: str) -> str:
    lines = original.splitlines(keepends=True)
    ending = "\n" if lines[line_index].endswith("\n") else ""
    lines[line_index] = f"GITLAB_TOKEN={token}{ending}"
    return "".join(lines)


def _inject_token(path: Path, original: str, injected: str) -> None:
    current, _ = _read_secure_env(path)
    if current != original:
        raise ProvisionError("env file changed after preflight; refusing to overwrite it")
    _atomic_replace(path, injected)


@contextlib.contextmanager
def _exclusive_env_lock(path: Path):
    parent = path.parent
    try:
        metadata = parent.stat()
    except OSError as exc:
        raise ProvisionError("env parent directory must already exist") from exc
    if (parent.is_symlink() or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o022):
        raise ProvisionError("env parent directory must be current-user-owned and not group/world writable")
    lock_path = parent / ".gitlab-agent-token-provision.lock"
    fd = None
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lock_metadata = os.fstat(fd)
        if (not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_uid != os.getuid()
                or stat.S_IMODE(lock_metadata.st_mode) != 0o600):
            raise ProvisionError("provisioning lock must be current-user-owned and mode 0600")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException as exc:
        if fd is not None:
            os.close(fd)
        if isinstance(exc, ProvisionError):
            raise
        raise ProvisionError("another GitLab token provisioning transaction is active") from exc
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextlib.contextmanager
def _exclusive_receipt_lock(path: Path):
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = parent.stat()
    if (parent.is_symlink() or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077):
        raise ProvisionError("receipt directory must be current-user-owned and mode 0700 or stricter")
    digest = hashlib.sha256(str(path).encode()).hexdigest()
    lock_path = parent / f".gitlab-token-receipt-{digest}.lock"
    fd = None
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lock_metadata = os.fstat(fd)
        if (not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_uid != os.getuid()
                or stat.S_IMODE(lock_metadata.st_mode) != 0o600):
            raise ProvisionError("receipt lock must be current-user-owned and mode 0600")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException as exc:
        if fd is not None:
            os.close(fd)
        if isinstance(exc, ProvisionError):
            raise
        raise ProvisionError("another transaction owns this provisioning receipt") from exc
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise ProvisionError("receipt path must be absolute and must not already exist")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ProvisionError("receipt directory must not be a symlink")
    parent_metadata = path.parent.stat()
    if (not stat.S_ISDIR(parent_metadata.st_mode) or parent_metadata.st_uid != os.getuid()
            or stat.S_IMODE(parent_metadata.st_mode) & 0o077):
        raise ProvisionError("receipt directory must be current-user-owned and mode 0700 or stricter")
    fd = None
    created_identity: tuple[int, int] | None = None
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created_metadata = os.fstat(fd)
        created_identity = (created_metadata.st_dev, created_metadata.st_ino)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(receipt, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if fd is not None:
            os.close(fd)
        if created_identity is not None:
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == created_identity:
                    path.unlink()
            except FileNotFoundError:
                pass
        raise


def _unlink_receipt_if_owned(path: Path, operation_id: str) -> None:
    try:
        raw, metadata = _read_secure_env(path)
    except FileNotFoundError:
        return
    except ProvisionError as exc:
        if not path.exists():
            return
        raise ProvisionError("receipt cleanup could not verify file ownership") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProvisionError("receipt cleanup refused an unverified file") from exc
    if not isinstance(value, dict) or value.get("operation_id") != operation_id:
        raise ProvisionError("receipt cleanup refused a file owned by another operation")
    current = path.lstat()
    if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise ProvisionError("receipt changed during cleanup; refusing to unlink it")
    path.unlink()


def _validate_expiry(value: str) -> str:
    try:
        expires = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ProvisionError("expires-at must be YYYY-MM-DD") from exc
    today = dt.datetime.now(dt.timezone.utc).date()
    if expires <= today or expires > today + dt.timedelta(days=365):
        raise ProvisionError("expires-at must be within the next 365 days")
    return value


def _list_project_tokens(glab: GlabClient, project_id: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for page in range(1, 101):
        items = glab.request_list(f"/projects/{project_id}/access_tokens?per_page=100&page={page}")
        result.extend(items)
        if len(items) < 100:
            return result
    raise ProvisionError("project access token listing exceeded 100 pages")


def _revoke_unique_token_named(glab: GlabClient, project_id: int, token_name: str) -> int:
    candidates = [
        item for item in _list_project_tokens(glab, project_id)
        if item.get("name") == token_name and isinstance(item.get("id"), int)
        and item.get("revoked") is not True
    ]
    if len(candidates) != 1:
        raise ProvisionError(f"expected one exact operation token during reconciliation, found {len(candidates)}")
    token_id = candidates[0]["id"]
    glab.request(
        f"/projects/{project_id}/access_tokens/{token_id}",
        method="DELETE",
        allow_empty=True,
    )
    return token_id


def _replace_json(path: Path, value: dict[str, Any]) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_replace(path, content)


def provision(
    args: argparse.Namespace,
    *,
    glab: GlabClient | None = None,
    token_client_factory: Callable[[str, str], AgentTokenClient] = AgentTokenClient,
) -> dict[str, Any]:
    if AGENT_RUNTIME_MARKERS.intersection(os.environ):
        raise ProvisionError("operator-only helper refuses to run inside a registered Buzz Agent runtime")
    env_path = Path(args.env_file).expanduser()
    receipt_path = Path(args.receipt).expanduser()
    if not receipt_path.is_absolute():
        raise ProvisionError("receipt path must be absolute")
    receipt_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # macOS exposes temporary directories through a ``/var`` symlink.  Compare
    # canonical paths so a secure absolute receipt under /var is not rejected
    # merely because its spelling differs from /private/var.
    if receipt_path.resolve(strict=False) != receipt_path.parent.resolve(strict=True) / receipt_path.name:
        raise ProvisionError("receipt path must use a canonical non-symlink parent")
    with _exclusive_env_lock(env_path):
        with _exclusive_receipt_lock(receipt_path):
            return _provision_locked(args, glab=glab, token_client_factory=token_client_factory)


def _provision_locked(
    args: argparse.Namespace,
    *,
    glab: GlabClient | None,
    token_client_factory: Callable[[str, str], AgentTokenClient],
) -> dict[str, Any]:
    profile = PROFILES[args.profile]
    want_external = resolve_external(profile, getattr(args, "external", "auto"))
    if not SAFE_NAME.fullmatch(args.agent_name) or not SAFE_NAME.fullmatch(args.token_name):
        raise ProvisionError("agent-name and token-name must use letters, digits, dot, underscore or dash")
    if not args.authorization_ref.strip() or "\n" in args.authorization_ref or len(args.authorization_ref) > 200:
        raise ProvisionError("authorization-ref must be a non-empty single line of at most 200 characters")
    expires_at = _validate_expiry(args.expires_at)
    env_path = Path(args.env_file).expanduser()
    receipt_path = Path(args.receipt).expanduser()
    if not receipt_path.is_absolute() or receipt_path.exists() or receipt_path.is_symlink():
        raise ProvisionError("receipt path must be absolute and must not already exist")
    pending_prefix = f".{receipt_path.name}."
    if any(item.name.startswith(pending_prefix) and item.name.endswith(".pending.json")
           for item in receipt_path.parent.iterdir()):
        raise ProvisionError("an unresolved pending journal exists for this receipt; reconcile it before retrying")
    original_env, placeholder_index = _read_env_placeholder(env_path)
    glab = glab or GlabClient(args.host, glab_path=args.glab)

    operator = glab.request("/user")
    if operator.get("username") != args.authorized_admin or operator.get("is_admin") is not True:
        raise ProvisionError("local glab identity must match --authorized-admin and be a GitLab instance admin")
    encoded_project = urllib.parse.quote(args.project, safe="")
    project = glab.request(f"/projects/{encoded_project}")
    project_id = project.get("id")
    if not isinstance(project_id, int) or project.get("path_with_namespace") != args.project:
        raise ProvisionError("project lookup did not match the exact requested project")
    operation_id = secrets.token_hex(16)
    server_token_name = f"{args.token_name}--op-{operation_id}"
    if any(item.get("name") == server_token_name and item.get("revoked") is not True
           for item in _list_project_tokens(glab, project_id)):
        raise ProvisionError("the generated operation token name unexpectedly already exists")

    journal_path = receipt_path.with_name(f".{receipt_path.name}.{operation_id}.pending.json")
    journal: dict[str, Any] = {
        "schema_version": "1.0",
        "state": "PENDING",
        "operation_id": operation_id,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "authorization_evidence_ref": args.authorization_ref,
        "requested": {
            "host": args.host, "project_id": project_id, "project_path": args.project,
            "agent": args.agent_name, "profile": args.profile, "access_level": profile.access_level,
            "scopes": list(profile.scopes), "expires_at": expires_at,
            "requested_token_name": args.token_name, "server_token_name": server_token_name,
            "env_file": str(env_path), "receipt": str(receipt_path),
            "original_env_sha256": hashlib.sha256(original_env.encode()).hexdigest(),
        },
        "secret_material_in_journal": False,
    }
    _write_receipt(journal_path, journal)

    created: dict[str, Any] | None = None
    injected_env: str | None = None
    receipt_write_attempted = False
    remote_safe = True
    env_safe = True
    try:
        remote_safe = False
        created = glab.request(
            f"/projects/{project_id}/access_tokens",
            method="POST",
            payload={
                "name": server_token_name,
                "scopes": list(profile.scopes),
                "access_level": profile.access_level,
                "expires_at": expires_at,
            },
        )
        token_id, bot_user_id, secret = created.get("id"), created.get("user_id"), created.get("token")
        journal["created_token"] = {
            "token_id": token_id if isinstance(token_id, int) else None,
            "bot_user_id": bot_user_id if isinstance(bot_user_id, int) else None,
            "token_sha256": hashlib.sha256(secret.encode()).hexdigest() if isinstance(secret, str) else None,
        }
        _replace_json(journal_path, journal)
        if not isinstance(token_id, int) or not isinstance(bot_user_id, int) or not isinstance(secret, str) or not secret:
            raise ProvisionError("GitLab token response is missing id, user_id or token")
        if (created.get("name") != server_token_name or created.get("expires_at") != expires_at
                or created.get("access_level") != profile.access_level
                or set(created.get("scopes", [])) != set(profile.scopes)):
            raise ProvisionError("GitLab created a token outside the exact authorized parameters")

        glab.request(f"/users/{bot_user_id}", method="PUT", payload={"external": want_external})
        bot = glab.request(f"/users/{bot_user_id}")
        if bot.get("external") is not want_external or bot.get("state") != "active":
            raise ProvisionError(
                f"project token bot was not verified as active and {'external' if want_external else 'non-external'}")

        token_client = token_client_factory(args.host, secret)
        token_user = token_client.get_object("/user")
        target = token_client.get_object(f"/projects/{project_id}")
        memberships = token_client.get_all("/projects", {"membership": "true", "simple": "true"})
        membership_ids = sorted(item.get("id") for item in memberships if isinstance(item.get("id"), int))
        if want_external:
            internal = token_client.get_all("/projects", {"visibility": "internal", "simple": "true"})
            unexpected_internal: list[int] | None = sorted(
                item.get("id") for item in internal
                if isinstance(item.get("id"), int) and item.get("id") != project_id
            )
        else:
            unexpected_internal = None  # a non-external bot reads every Internal project by design
        if token_user.get("id") != bot_user_id or target.get("id") != project_id:
            raise ProvisionError("new token identity or target project verification failed")
        if membership_ids != [project_id] or unexpected_internal:
            raise ProvisionError("new token is not isolated to exactly the target project")

        injected_env = _render_injected_env(original_env, placeholder_index, secret)
        env_safe = False
        _inject_token(env_path, original_env, injected_env)
        current_env, metadata = _read_secure_env(env_path)
        if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid():
            raise ProvisionError("env file security metadata changed during token injection")
        if current_env != injected_env:
            raise ProvisionError("env file token injection readback failed")
        receipt = {
            "schema_version": "1.0",
            "operation_id": operation_id,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "authorization": {
                "admin_id": operator.get("id"),
                "admin_username": operator.get("username"),
                "reference": args.authorization_ref,
            },
            "agent": args.agent_name,
            "gitlab": {
                "host": args.host,
                "project_id": project_id,
                "project_path": args.project,
                "token_id": token_id,
                "requested_token_name": args.token_name,
                "token_name": created.get("name"),
                "bot_user_id": bot_user_id,
                "bot_username": bot.get("username"),
                "bot_external": want_external,
                "internal_isolation_checked": want_external,
                "access_level": profile.access_level,
                "scopes": list(profile.scopes),
                "expires_at": created.get("expires_at"),
                "membership_project_ids": membership_ids,
                "unexpected_internal_project_ids": unexpected_internal,
            },
            "secret_material_in_receipt": False,
        }
        receipt_write_attempted = True
        _write_receipt(receipt_path, receipt)
        journal_path.unlink()
        return receipt
    except BaseException as exc:
        cleanup_errors: list[str] = []
        if not env_safe and injected_env is not None:
            try:
                current_env, _ = _read_secure_env(env_path)
                if current_env != injected_env:
                    raise ProvisionError("env no longer matches this operation; refusing destructive rollback")
                _atomic_replace(env_path, original_env)
                env_safe = True
            except BaseException as rollback_exc:  # pragma: no cover - catastrophic I/O path
                cleanup_errors.append(f"env rollback failed: {rollback_exc}")
        if receipt_write_attempted:
            try:
                _unlink_receipt_if_owned(receipt_path, operation_id)
            except BaseException as rollback_exc:  # pragma: no cover - catastrophic I/O path
                cleanup_errors.append(f"receipt rollback failed: {rollback_exc}")
        if not remote_safe:
            try:
                if created is not None and isinstance(created.get("id"), int):
                    glab.request(
                        f"/projects/{project_id}/access_tokens/{created['id']}",
                        method="DELETE",
                        allow_empty=True,
                    )
                else:
                    _revoke_unique_token_named(glab, project_id, server_token_name)
                remote_safe = True
            except BaseException as revoke_exc:
                cleanup_errors.append(f"token revocation failed: {revoke_exc}")
        if remote_safe and env_safe and not cleanup_errors:
            try:
                journal_path.unlink()
            except BaseException as journal_exc:  # pragma: no cover - catastrophic I/O path
                cleanup_errors.append(f"pending journal cleanup failed: {journal_exc}")
        else:
            journal["state"] = "MANUAL_RECONCILIATION_REQUIRED"
            journal["cleanup_errors"] = cleanup_errors
            try:
                _replace_json(journal_path, journal)
            except BaseException as journal_exc:  # pragma: no cover - catastrophic I/O path
                cleanup_errors.append(f"pending journal update failed: {journal_exc}")
        if not isinstance(exc, Exception):
            raise
        message = str(exc) if isinstance(exc, ProvisionError) else "unexpected provisioning failure"
        if cleanup_errors:
            message += "; " + "; ".join(cleanup_errors)
        raise ProvisionError(message) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--project", required=True, help="Exact path_with_namespace")
    parser.add_argument("--agent-name", required=True)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--external", choices=EXTERNAL_CHOICES, default="auto",
                        help="auto (default): planner/reporter external, developer non-external because MR pipelines must "
                             "read the Internal CI config project. 'no' also skips the Internal-visibility check: "
                             "only for identities that submit MRs, never for planner/reporter")
    parser.add_argument("--token-name", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--authorized-admin", required=True)
    parser.add_argument("--authorization-ref", required=True, help="Non-secret evidence reference; not an authorization capability")
    parser.add_argument("--glab", help="Optional exact glab executable path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = provision(args)
    except ProvisionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    gitlab = receipt["gitlab"]
    print(
        "OK: provisioned token "
        f"id={gitlab['token_id']} bot_user_id={gitlab['bot_user_id']} "
        f"project={gitlab['project_path']} receipt={Path(args.receipt).expanduser()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
