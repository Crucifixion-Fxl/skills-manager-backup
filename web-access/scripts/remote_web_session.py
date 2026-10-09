#!/usr/bin/env python3
"""Plan a loopback browser session a person can take over through ssh -L.

Chrome remote debugging and x11vnc listen on 127.0.0.1 only. The login profile
is either temporary or explicitly saved for one site/account. Cards and JSON omit cookies
and tokens.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


class PlanError(Exception):
    """The plan would widen the listen address, leak a path, or reuse a profile."""


_BATCH = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_USER = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_HOST = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
_OPT = re.compile(r"^[A-Za-z0-9=,@:._/-]+$")
_DISPLAY = re.compile(r"^:[0-9]{1,4}$")
_SSH_CONFIG = re.compile(
    r"^(?:IdentitiesOnly=(?:yes|no)"
    r"|StrictHostKeyChecking=(?:yes|no|accept-new)"
    r"|UserKnownHostsFile=/dev/null)$"
)
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+"
    r"|\b(?:set-cookie|cookie|authorization|session[-_]?id"
    r"|access[-_]?token|refresh[-_]?token|id[-_]?token|token)\b"
    r"\s*[:=]\s*(?:bearer\s+)?\S+)"
)
_V4_LOOPBACK = "0100007F"
_V6_LOOPBACK = "00000000000000000000000001000000"


def _port(port: int) -> int:
    if isinstance(port, bool):
        raise PlanError("port must be 1-65535")
    try:
        number = int(port)
    except (TypeError, ValueError) as exc:
        raise PlanError("port must be 1-65535") from exc
    if number < 1 or number > 65535:
        raise PlanError("port must be 1-65535")
    return number


def _bind(bind: str) -> str:
    if bind != "127.0.0.1":
        raise PlanError("bind must be 127.0.0.1")
    return bind


def chrome_argv(chrome: str, profile: Path, port: int, bind: str = "127.0.0.1") -> list[str]:
    """Argv for a headless Chrome whose DevTools port stays on loopback."""
    number = _port(port)
    address = _bind(bind)
    return [
        str(chrome),
        "--headless=new",
        f"--remote-debugging-address={address}",
        f"--remote-debugging-port={number}",
        f"--user-data-dir={profile}",
        f"--remote-allow-origins=http://127.0.0.1:{number}",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]


def vnc_argv(binary: str, display: str, port: int, bind: str = "127.0.0.1") -> list[str]:
    """Argv for x11vnc. No password flag: a password would show up in the process list."""
    number = _port(port)
    address = _bind(bind)
    if not _DISPLAY.fullmatch(display):
        raise PlanError("display must look like :99")
    return [
        str(binary),
        "-display", display,
        "-localhost",
        "-listen", address,
        "-rfbport", str(number),
        "-nopw",
        "-forever",
        "-shared",
    ]


def _forward(port: int) -> str:
    number = _port(port)
    return f"127.0.0.1:{number}:127.0.0.1:{number}"


def _identity_path(identity: str) -> str:
    if (
        not isinstance(identity, str)
        or not identity.startswith("/")
        or not _OPT.fullmatch(identity)
        or "0.0.0.0" in identity
    ):
        raise PlanError("identity path is not allowed")
    return identity


def _extra_ssh(extra_options: tuple[str, ...] | list[str]) -> list[str]:
    """Only the identity file and the three options the tunnel test needs."""
    extra = tuple(extra_options)
    allowed: list[str] = []
    index = 0
    while index < len(extra):
        item = extra[index]
        if item == "-i" and index + 1 < len(extra):
            allowed.extend(["-i", _identity_path(extra[index + 1])])
            index += 2
            continue
        if item == "-o" and index + 1 < len(extra) and _SSH_CONFIG.fullmatch(extra[index + 1]):
            allowed.extend(["-o", extra[index + 1]])
            index += 2
            continue
        raise PlanError("ssh option is not allowed")
    return allowed


def ssh_forward_args(
    port: int,
    user: str,
    host: str,
    identity: str | None = None,
    extra_options: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """ssh -L whose local and remote ends are both 127.0.0.1."""
    if not _USER.fullmatch(user) or not _HOST.fullmatch(host):
        raise PlanError("ssh user or host is not allowed")
    argv = [
        "ssh", "-N",
        "-L", _forward(port),
        "-o", "ExitOnForwardFailure=yes",
        "-o", "BatchMode=yes",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-o", "ControlPersist=no",
        "-o", "GatewayPorts=no",
    ]
    if identity is not None:
        argv.extend(["-i", _identity_path(identity)])
    argv.extend(_extra_ssh(extra_options))
    argv.append(f"{user}@{host}")
    return argv


def redact(text: str) -> str:
    return _SECRET.sub("[redacted]", text)


def _one_line_site(site: str) -> str:
    if not isinstance(site, str) or site == "" or len(site) > 200:
        raise PlanError("site must be one line")
    if any(ord(char) < 32 or ord(char) == 127 for char in site):
        raise PlanError("site must be one line")
    return redact(site)


def _require_root(root: Path) -> Path:
    root = Path(root)
    if not root.is_absolute():
        raise PlanError("root must be an absolute path")
    if root.is_symlink() or root.resolve() != root:
        raise PlanError("root must not be a symlink")
    if not root.is_dir():
        raise PlanError("root must be a directory")
    info = root.stat()
    if info.st_uid != os.getuid():
        raise PlanError("root must be owned by the current user")
    if (info.st_mode & 0o777) != 0o700:
        raise PlanError("root must be mode 0700")
    return root


def _batch_dir(root: Path, batch: str) -> Path:
    root = _require_root(root)
    if not isinstance(batch, str) or not _BATCH.fullmatch(batch):
        raise PlanError("batch id is not allowed")
    profile = root / batch
    if profile.parent.resolve() != root.resolve():
        raise PlanError("batch escapes the root")
    return profile


def allocate_profile(root: Path, batch: str) -> Path:
    profile = _batch_dir(root, batch)
    if profile.exists() or profile.is_symlink():
        raise PlanError("batch profile already exists")
    profile.mkdir(mode=0o700)
    profile.chmod(0o700)
    if profile.is_symlink() or (profile.stat().st_mode & 0o777) != 0o700:
        raise PlanError("profile is not a private directory")
    return profile


def session_profile(root: Path, batch: str, *, site: str, account: str = '', retention: str = 'once',
                    resume: bool = False, days: int = 7, now: float | None = None) -> Path:
    """Retention is an explicit user choice; a saved browser is still subject to identity verification."""
    if retention not in ('once', 'save') or not 1 <= days <= 90:
        raise PlanError('invalid session retention')
    site = _one_line_site(site)
    if account:
        account = _one_line_site(account)
    if retention == 'save' and not account:
        raise PlanError('saved session needs an account label')
    timestamp = time.time() if now is None else now
    profile = _batch_dir(root, batch)
    if resume:
        if retention != 'save' or profile.is_symlink() or not profile.is_dir():
            raise PlanError('only an explicitly saved session may be resumed')
        info = profile.stat()
        if info.st_uid != os.getuid() or (info.st_mode & 0o777) != 0o700:
            raise PlanError('saved session is not private')
        metadata = profile / '.addx-session.json'
        if metadata.is_symlink() or not metadata.is_file() or metadata.stat().st_mode & 0o077:
            raise PlanError('invalid saved session metadata')
        try:
            saved = json.loads(metadata.read_text())
        except Exception as exc:
            raise PlanError('invalid saved session metadata') from exc
        if (saved.get('retention') != 'save' or saved.get('site') != site or saved.get('account') != account
                or saved.get('expiresAt', 0) <= timestamp):
            raise PlanError('saved session expired or belongs to another site/account')
        if (profile / 'SingletonLock').exists() or (profile / 'SingletonLock').is_symlink():
            raise PlanError('close the owning browser before resuming')
        return profile
    profile = allocate_profile(root, batch)
    metadata = {'retention': retention, 'site': site, 'account': account,
                'expiresAt': timestamp + days * 86400 if retention == 'save' else timestamp}
    with (profile / '.addx-session.json').open('x') as output:
        os.chmod(output.name, 0o600)
        json.dump(metadata, output, ensure_ascii=False)
    return profile


def delete_profile(root: Path, batch: str) -> None:
    profile = _batch_dir(root, batch)
    if profile.is_symlink():
        raise PlanError("refusing to delete a symlink")
    if not profile.exists():
        return
    if not profile.is_dir():
        raise PlanError("profile is not a directory")
    shutil.rmtree(profile)


def takeover_card(mode: str, port: int, ssh_user: str, ssh_host: str, site: str) -> str:
    number = _port(port)
    safe_site = _one_line_site(site)
    if mode == "cdp":
        reach = f"http://127.0.0.1:{number}"
    elif mode == "vnc":
        reach = f"vnc://127.0.0.1:{number}"
    else:
        raise PlanError("mode must be cdp or vnc")
    command = " ".join(ssh_forward_args(number, ssh_user, ssh_host))
    return "\n".join([
        f"站点：{safe_site}",
        f"接管：验证码和异常页由人在隧道这一侧打开 {reach}",
        command,
    ])


def loopback_only(tcp: str, tcp6: str, port: int) -> bool:
    """True only when every LISTEN row for this port is 127.0.0.1 or ::1."""
    number = _port(port)
    wanted = f"{number:04X}"
    found = False
    for text, loopback in ((tcp, _V4_LOOPBACK), (tcp6, _V6_LOOPBACK)):
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < 4 or fields[3].upper() != "0A":
                continue
            local = fields[1]
            if ":" not in local:
                continue
            address, local_port = local.rsplit(":", 1)
            if local_port.upper() != wanted:
                continue
            if address.upper() != loopback:
                return False
            found = True
    return found


def _plan(args: argparse.Namespace) -> dict:
    """Validate the bind before creating a profile."""
    number = _port(args.port)
    address = _bind(args.bind)
    if args.mode == "cdp":
        endpoint = f"http://127.0.0.1:{number}"
        argv = chrome_argv(args.chrome, Path(args.root) / args.batch, number, address)
    elif args.mode == "vnc":
        endpoint = f"vnc://127.0.0.1:{number}"
        argv = vnc_argv(args.vnc, args.display, number, address)
    else:
        raise PlanError("mode must be cdp or vnc")
    ssh = ssh_forward_args(number, args.ssh_user, args.ssh_host)
    card = takeover_card(args.mode, number, args.ssh_user, args.ssh_host, args.site)
    viewer_port = _port(getattr(args, 'viewer_port', 19827))
    control_port = _port(getattr(args, 'control_port', 9222))
    if args.mode == 'vnc' and len({number, viewer_port, control_port}) != 3:
        raise PlanError('VNC, viewer and control ports must be distinct')
    view_url = getattr(args, 'view_url', None)
    if view_url:
        url = urlparse(view_url)
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password
                or url.query or url.fragment):
            raise PlanError('view URL must be a configured loopback noVNC page without secrets')
    action = _one_line_site(getattr(args, 'action', '完成登录或当前页面验证'))
    reason = getattr(args, 'reason', 'login')
    if reason not in ('login', 'verification', 'consent', 'manual'):
        raise PlanError('invalid handoff reason')
    profile = session_profile(Path(args.root), args.batch, site=args.site, account=getattr(args, 'account', ''),
                              retention=getattr(args, 'retention', 'once'), resume=getattr(args, 'resume', False),
                              days=getattr(args, 'session_days', 7))
    retention = getattr(args, 'retention', 'once')
    reach = view_url or (f'http://127.0.0.1:{viewer_port}/' if args.mode == 'vnc' else endpoint)
    user_prompt = (f'请在自己的电脑上打开浏览器画面：{reach}。\n'
                   f'网站：{_one_line_site(args.site)}。请在当前画面中{action}，完成后回复“完成”。\n'
                   + ('会话选择：一次性，本次用完清除。' if retention == 'once'
                      else f'会话选择：保存供后续复用，账号 {_one_line_site(args.account)}，最长 {args.session_days} 天；可以随时要求清除。'))
    return {
        "state": "PLANNED",
        "bind": address,
        "mode": args.mode,
        "endpoint": endpoint,
        "argv": argv,
        "profile": str(profile),
        "ssh": ssh,
        "card": card,
        "prompt": user_prompt,
        "handoff": {"reason": reason, "site": _one_line_site(args.site), "action": action,
                    "account": _one_line_site(args.account) if getattr(args, 'account', '') else '',
                    "verification": "caller-required", "completionSignal": "user-reply",
                    "retention": retention},
        "retention": retention,
        "resumed": getattr(args, 'resume', False),
        **({"desktop_argv": ["Xvfb", args.display, "-screen", "0", "1280x800x24", "-nolisten", "tcp", "-s", "0"],
            "viewer_argv": ["node", str(Path(__file__).resolve().parents[1] / "runtime/bin/web-view.js"),
                            f"--vnc-port={number}", f"--port={viewer_port}",
                            f"--capability-file={profile / 'viewer-capability.json'}"],
            "viewer_connect_argv": ["web-view", f"--ssh-target={args.ssh_user}@{args.ssh_host}",
                                    f"--port={viewer_port}", f"--capability-file={profile / 'viewer-capability.json'}"],
            "viewer_ssh": ssh_forward_args(viewer_port, args.ssh_user, args.ssh_host),
            "browser_env": {"DISPLAY": args.display},
            "browser_argv": [arg for arg in chrome_argv(args.chrome, profile, getattr(args, 'control_port', 9222))
                             if arg != '--headless=new']} if args.mode == 'vnc' else {}),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="remote_web_session.py")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--mode", default="vnc", choices=("cdp", "vnc"))
    plan.add_argument("--batch", required=True)
    plan.add_argument("--port", required=True, type=int)
    plan.add_argument("--root", required=True)
    plan.add_argument("--chrome", default="/usr/bin/google-chrome")
    plan.add_argument("--vnc", default="/usr/bin/x11vnc")
    plan.add_argument("--display", default=":99")
    plan.add_argument("--ssh-user", required=True)
    plan.add_argument("--ssh-host", required=True)
    plan.add_argument("--site", required=True)
    plan.add_argument("--reason", choices=("login", "verification", "consent", "manual"), default="login")
    plan.add_argument("--action", default="完成登录或当前页面验证")
    plan.add_argument("--bind", default="127.0.0.1")
    plan.add_argument("--retention", choices=("once", "save"), default="once")
    plan.add_argument("--account", default="")
    plan.add_argument("--session-days", type=int, default=7)
    plan.add_argument("--resume", action="store_true")
    plan.add_argument("--view-url")
    plan.add_argument("--viewer-port", type=int, default=19827)
    plan.add_argument("--control-port", type=int, default=9222)
    delete = commands.add_parser("delete")
    delete.add_argument("--root", required=True)
    delete.add_argument("--batch", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        if args.command == "plan":
            sys.stdout.write(json.dumps(_plan(args), ensure_ascii=False) + "\n")
        elif args.command == "delete":
            delete_profile(Path(args.root), args.batch)
            sys.stdout.write(json.dumps({"deleted": True}) + "\n")
        else:
            raise PlanError("unknown command")
        return 0
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        print("failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
