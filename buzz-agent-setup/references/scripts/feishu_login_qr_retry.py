#!/usr/bin/env python3
"""Send a Feishu browser login QR to the owner, retrying only until login succeeds.

Run beside feishu_browser.js, which writes state.txt, ready.txt and qr.png.
The caller supplies the installed Node and lark-cli entry paths explicitly.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path


QR_STATE = re.compile(r"QR (\d+) \d+")


def logged_in(directory: Path) -> bool:
    state = directory / "state.txt"
    return (directory / "ready.txt").is_file() and state.is_file() and state.read_text().startswith("LOGGED_IN ")


def qr_number(directory: Path) -> int | None:
    state = directory / "state.txt"
    if not state.is_file() or not (match := QR_STATE.fullmatch(state.read_text().strip())):
        return None
    return int(match.group(1))


def send_and_readback(args: argparse.Namespace, flag: str, value: str) -> None:
    base = [args.node, args.cli_entry, "im"]
    common = ["--profile", args.profile, "--as", "user"]
    result = subprocess.run(base + ["+messages-send", "--user-id", args.recipient_user_id, flag, value] + common,
                            cwd=args.browser_dir, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout[result.stdout.find("{"):])
    message_id = (payload.get("data") or {}).get("message_id")
    if not payload.get("ok") or not isinstance(message_id, str) or not message_id.startswith("om_"):
        raise RuntimeError("QR message send returned no receipt")
    readback = subprocess.run(base + ["+messages-mget", "--message-ids", message_id] + common,
                              cwd=args.browser_dir, capture_output=True, text=True, check=True)
    body = json.loads(readback.stdout[readback.stdout.find("{"):])
    if not body.get("ok") or not any(item.get("message_id") == message_id
                                     for item in (body.get("data") or {}).get("messages", [])):
        raise RuntimeError("QR message readback failed")


def wait_for_qr(directory: Path, previous: int | None, timeout: float, poll: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if logged_in(directory):
            raise StopIteration
        number = qr_number(directory)
        if number is not None and number != previous and (directory / "qr.png").is_file():
            return number
        time.sleep(poll)
    raise RuntimeError("browser did not produce a fresh QR")


def run(args: argparse.Namespace) -> int:
    directory = args.browser_dir
    if not directory.is_dir() or directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise RuntimeError("browser directory must be a private 0700 directory")
    previous = None
    for attempt in range(1, args.max_sends + 1):
        if logged_in(directory):
            print("LOGIN_OK")
            return 0
        if attempt > 1:
            (directory / "shoot.txt").touch()
        try:
            previous = wait_for_qr(directory, previous, args.qr_timeout_seconds, args.poll_seconds)
        except StopIteration:
            print("LOGIN_OK")
            return 0
        send_and_readback(args, "--text", f"飞书开发平台登录二维码 {attempt}/{args.max_sends}。请用飞书移动端扫码并确认登录；上一张码如已过期，请使用下一张。")
        send_and_readback(args, "--image", "./qr.png")
        print(f"QR_SENT {attempt}/{args.max_sends}", flush=True)
        deadline = time.monotonic() + args.resend_seconds
        while time.monotonic() < deadline:
            if logged_in(directory):
                print("LOGIN_OK")
                return 0
            time.sleep(args.poll_seconds)
    if logged_in(directory):
        print("LOGIN_OK")
        return 0
    print(f"LOGIN_NOT_COMPLETED_AFTER_{args.max_sends}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-dir", type=Path, required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--cli-entry", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--recipient-user-id", required=True)
    parser.add_argument("--max-sends", type=int, default=10)
    parser.add_argument("--resend-seconds", type=float, default=95)
    parser.add_argument("--qr-timeout-seconds", type=float, default=20)
    parser.add_argument("--poll-seconds", type=float, default=1)
    args = parser.parse_args()
    if not 1 <= args.max_sends <= 10 or args.resend_seconds <= 0 or args.poll_seconds <= 0:
        parser.error("max-sends must be 1..10 and intervals must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
