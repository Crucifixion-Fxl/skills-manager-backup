#!/usr/bin/env python3
"""Publish a Markdown report as a Feishu doc with the agent's own bot, and share it read-only with a group.

Usage: feishu_doc_publish.py --title <title> --chat-id <oc_...> [--retries N] [--timeout SECONDS] [--lark-cli PATH] [--buzz PATH] < report.md
Env:   LARKSUITE_CLI_CONFIG_DIR / LARKSUITE_CLI_DATA_DIR pick the agent's own lark-cli profile (never the owner's).
Out:   {"document_id": ..., "url": ...} on stdout, exit 0. Any failure exits non-zero with nothing on stdout, and the
       document is NOT shared with the group unless every part of the body reached it.

What it does that a bare `lark-cli docs +create` does not (see references/feishu-doc-report.md):
- images hosted on Buzz (`![](https://…/media/<sha>…)`) need Buzz auth, so Feishu cannot fetch them and `+create` times out after
  30s; they are downloaded with `buzz media get` and referenced as local `@./…` files (lark-cli only reads paths inside the cwd);
- `<`, `~` and `$` are escaped outside code fences (Markdown import treats them as XML tags / strikethrough / math);
- the body goes out in chunks that each end at one image (`+create`, then `+update --command append`), with retries.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

MEDIA_IMAGE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]*/media/([0-9a-f]{64})[^)\s]*)\)")


def fail(message: str) -> "None":
    print(f"feishu_doc_publish: {message}", file=sys.stderr)
    sys.exit(1)


def run_bounded(cmd: list[str], body: str | None, timeout: float) -> subprocess.CompletedProcess:
    """subprocess.run with a hard deadline that also kills grandchildren holding the pipes (node wrappers do)."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        out, err = proc.communicate(body, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def fence_marker(line: str) -> str | None:
    stripped = line.lstrip()
    for marker in ("```", "~~~"):
        if stripped.startswith(marker):
            return marker
    return None


def escape(line: str) -> str:
    return re.sub(r"(?<!\\)~", r"\\~", line.replace("<", "\\<").replace("$", "\\$"))


def escape_outside_fences(text: str) -> str:
    out, fence = [], None
    for line in text.split("\n"):
        marker = fence_marker(line)
        if marker and fence in (None, marker):
            fence = None if fence else marker
            out.append(line)
        elif fence or line.startswith("!["):
            out.append(line)
        else:
            out.append(escape(line))
    return "\n".join(out)


def paragraphs(text: str) -> list[str]:
    """Blocks separated by blank lines; blank lines inside a code fence do not split."""
    blocks, cur, fence = [], [], None
    for line in text.split("\n"):
        marker = fence_marker(line)
        if marker and fence in (None, marker):
            fence = None if fence else marker
        if not line.strip() and not fence:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def chunks(text: str) -> list[str]:
    out, cur = [], []
    for block in paragraphs(text):
        cur.append(block)
        if block.startswith("!["):
            out.append("\n\n".join(cur) + "\n")
            cur = []
    if cur:
        out.append("\n\n".join(cur) + "\n")
    return out or ["\n"]


class Lark:
    def __init__(self, binary: str, retries: int, timeout: float):
        self.binary, self.retries, self.timeout = binary, retries, timeout

    def run(self, args: list[str], body: str | None = None) -> dict:
        for attempt in range(self.retries):
            try:
                result = run_bounded([self.binary, *args], body, self.timeout)
            except subprocess.TimeoutExpired:
                print(f"feishu_doc_publish: {args[0]} {args[1]} timed out after {self.timeout:g}s "
                      f"(attempt {attempt + 1}/{self.retries})", file=sys.stderr)
                continue
            try:
                payload = json.loads(result.stdout)
            except ValueError:
                payload = None
            if result.returncode == 0 and isinstance(payload, dict) and payload.get("ok"):
                return payload
            print(f"feishu_doc_publish: {args[0]} {args[1]} failed (attempt {attempt + 1}/{self.retries})", file=sys.stderr)
        fail(f"{args[0]} {args[1]} kept failing")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", required=True)
    parser.add_argument("--chat-id", required=True, help="the Feishu group (oc_…) to share the document with, read-only")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120, help="seconds allowed for each lark-cli / buzz call")
    parser.add_argument("--lark-cli", default="lark-cli")
    parser.add_argument("--buzz", default="buzz")
    args = parser.parse_args()
    if not re.fullmatch(r"oc_[0-9a-f]+", args.chat_id):
        fail("--chat-id must look like oc_xxxxxxxx")

    text = sys.stdin.read()
    # lark-cli only reads @./ files inside the cwd, so the images live in a scratch directory under it
    scratch = Path(tempfile.mkdtemp(prefix=".feishu-doc-", dir=".")).resolve()
    try:
        def localize(match: re.Match) -> str:
            path = scratch / f"{match.group(2)}.jpg"
            if not path.exists():
                try:
                    got = run_bounded([args.buzz, "media", "get", match.group(1), "-o", str(path)], None, args.timeout)
                except subprocess.TimeoutExpired:
                    fail(f"downloading {match.group(1)} timed out after {args.timeout:g}s")
                if got.returncode != 0 or not path.exists():
                    fail(f"could not download {match.group(1)}")
            return f"\n\n![截图](@./{scratch.name}/{path.name})\n\n"

        parts = chunks(escape_outside_fences(MEDIA_IMAGE.sub(localize, text)))
        lark = Lark(args.lark_cli, args.retries, args.timeout)
        created = lark.run(["docs", "+create", "--as", "bot", "--doc-format", "markdown", "--title", args.title,
                            "--content", "-"], parts[0])["data"]["document"]
        for part in parts[1:]:
            lark.run(["docs", "+update", "--as", "bot", "--doc", created["document_id"], "--command", "append",
                      "--doc-format", "markdown", "--content", "-"], part)
        lark.run(["drive", "+member-add", "--as", "bot", "--token", created["document_id"], "--type", "docx",
                  "--member-type", "openchat", "--member-id", args.chat_id, "--perm", "view", "--yes"])
        print(json.dumps({"document_id": created["document_id"], "url": created["url"]}))
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
