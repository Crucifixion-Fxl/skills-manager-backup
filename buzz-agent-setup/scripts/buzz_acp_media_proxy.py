#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""ACP stdio proxy that turns Buzz imeta prompt text into inline image blocks.

This keeps stock buzz-acp unchanged. The proxy is installed under the original
adapter basename (for example ``claude-agent-acp``), starts the real adapter from
``BUZZ_ACP_MEDIA_ADAPTER_COMMAND``, and forwards newline-delimited JSON-RPC in
both directions. Protected media is downloaded by the stock Buzz CLI so the
agent identity's Blossom and NIP-OA credentials stay on the existing path.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, TextIO


SUPPORTED_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)
MAX_IMAGE_COUNT = 9
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30


class MediaProxyError(RuntimeError):
    pass


class Attachment(NamedTuple):
    url: str
    mime_type: str
    sha256: str
    size: int


def _attachment(tag: Any) -> Attachment | None:
    if not isinstance(tag, list) or not tag or tag[0] != "imeta":
        return None
    fields: dict[str, str] = {}
    for raw in tag[1:]:
        if not isinstance(raw, str) or " " not in raw:
            return None
        key, value = raw.split(" ", 1)
        if key in {"url", "m", "x", "size"}:
            if key in fields:
                return None
            fields[key] = value
    if set(fields) != {"url", "m", "x", "size"}:
        return None
    if fields["m"] not in SUPPORTED_MIME_TYPES:
        return None
    digest = fields["x"]
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        return None
    try:
        size = int(fields["size"])
    except ValueError:
        return None
    if size <= 0:
        return None
    return Attachment(fields["url"], fields["m"], digest, size)


def _tags_from_text(text: str) -> list[Any]:
    tags: list[Any] = []
    for line in text.splitlines():
        if not line.startswith("Tags: "):
            continue
        try:
            parsed = json.loads(line.removeprefix("Tags: "))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            tags.extend(parsed)
    return tags


def extract_attachments(message: Mapping[str, Any]) -> list[Attachment]:
    params = message.get("params")
    if not isinstance(params, Mapping):
        return []
    prompt = params.get("prompt")
    if not isinstance(prompt, list):
        return []
    attachments: list[Attachment] = []
    seen: set[str] = set()
    for block in prompt:
        if not isinstance(block, Mapping) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        for tag in _tags_from_text(text):
            item = _attachment(tag)
            if item is not None and item.sha256 not in seen:
                seen.add(item.sha256)
                attachments.append(item)
    return attachments


def _sniff_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _verify_bytes(item: Attachment, data: bytes) -> None:
    if item.size > MAX_IMAGE_BYTES or len(data) > MAX_IMAGE_BYTES:
        raise MediaProxyError("image exceeds per-image limit")
    if len(data) != item.size:
        raise MediaProxyError("downloaded image size does not match imeta")
    if hashlib.sha256(data).hexdigest() != item.sha256:
        raise MediaProxyError("downloaded image hash does not match imeta")
    if _sniff_mime(data) != item.mime_type:
        raise MediaProxyError("downloaded image type does not match imeta")


def augment_prompt(
    message: Mapping[str, Any],
    downloader: Callable[[Attachment], bytes],
    *,
    image_supported: bool,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Return an augmented copy and fail-soft media counters."""
    output = copy.deepcopy(dict(message))
    stats = {"found": 0, "added": 0, "failed": 0, "bytes": 0}
    if not image_supported:
        return output, stats
    attachments = extract_attachments(output)
    stats["found"] = len(attachments)
    prompt = output.get("params", {}).get("prompt")
    if not isinstance(prompt, list):
        return output, stats
    for item in attachments[:MAX_IMAGE_COUNT]:
        if item.size > MAX_IMAGE_BYTES or stats["bytes"] + item.size > MAX_TOTAL_IMAGE_BYTES:
            stats["failed"] += 1
            continue
        try:
            data = downloader(item)
            _verify_bytes(item, data)
        except (MediaProxyError, OSError, subprocess.SubprocessError):
            stats["failed"] += 1
            continue
        prompt.append(
            {
                "type": "text",
                "text": f"[Buzz image attachment: {item.sha256[:12]}]",
            }
        )
        prompt.append(
            {
                "type": "image",
                "data": base64.b64encode(data).decode("ascii"),
                "mimeType": item.mime_type,
            }
        )
        stats["added"] += 1
        stats["bytes"] += len(data)
    stats["failed"] += max(0, len(attachments) - MAX_IMAGE_COUNT)
    return output, stats


def adapter_image_capability(message: Mapping[str, Any]) -> bool | None:
    try:
        value = message["result"]["agentCapabilities"]["promptCapabilities"]["image"]
    except (KeyError, TypeError):
        return None
    return value if isinstance(value, bool) else None


class CapabilityState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._image_supported = False

    def observe(self, message: Mapping[str, Any]) -> None:
        supported = adapter_image_capability(message)
        if supported is not None:
            with self._lock:
                self._image_supported = supported

    def image_supported(self) -> bool:
        with self._lock:
            return self._image_supported


def cli_downloader(buzz_cli: str, item: Attachment) -> bytes:
    completed = subprocess.run(
        [buzz_cli, "media", "get", "--output", "-", item.url],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise MediaProxyError("Buzz CLI media download failed")
    return completed.stdout


def _forward_adapter_output(
    source: TextIO, destination: TextIO, state: CapabilityState
) -> None:
    try:
        for line in source:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                message = None
            if isinstance(message, Mapping):
                state.observe(message)
            destination.write(line)
            destination.flush()
    except BrokenPipeError:
        return


def proxy(
    adapter_command: str,
    adapter_args: Sequence[str],
    *,
    buzz_cli: str,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    environ: Mapping[str, str] = os.environ,
) -> int:
    child = subprocess.Popen(
        [adapter_command, *adapter_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=dict(environ),
    )
    if child.stdin is None or child.stdout is None:
        child.kill()
        raise MediaProxyError("failed to open adapter stdio")
    state = CapabilityState()
    output_thread = threading.Thread(
        target=_forward_adapter_output,
        args=(child.stdout, stdout, state),
        name="buzz-acp-media-proxy-output",
        daemon=True,
    )
    output_thread.start()
    try:
        for line in stdin:
            outbound = line
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                message = None
            if isinstance(message, Mapping) and isinstance(message.get("params"), Mapping):
                augmented, stats = augment_prompt(
                    message,
                    lambda item: cli_downloader(buzz_cli, item),
                    image_supported=state.image_supported(),
                )
                if stats["found"]:
                    print(
                        "buzz-acp media proxy: "
                        f"found={stats['found']} added={stats['added']} "
                        f"failed={stats['failed']} bytes={stats['bytes']}",
                        file=stderr,
                        flush=True,
                    )
                outbound = json.dumps(augmented, separators=(",", ":")) + "\n"
            child.stdin.write(outbound)
            child.stdin.flush()
    except BrokenPipeError:
        pass
    finally:
        child.stdin.close()
    returncode = child.wait()
    output_thread.join(timeout=5)
    return returncode


def _resolve_executable(value: str, label: str) -> str:
    if not value:
        raise MediaProxyError(f"{label} is required")
    resolved = shutil.which(value) if "/" not in value else value
    if not resolved:
        raise MediaProxyError(f"{label} is not executable")
    path = Path(resolved)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise MediaProxyError(f"{label} must be an absolute executable file")
    return str(path)


def main(arguments: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if any(argument in {"-h", "--help"} for argument in arguments):
        print(
            "usage: buzz_acp_media_proxy.py [ADAPTER_ARG ...]\n"
            "\nEnvironment: BUZZ_ACP_MEDIA_ADAPTER_COMMAND (required), "
            "BUZZ_ACP_MEDIA_BUZZ_CLI (default: buzz)"
        )
        return 0
    try:
        adapter = _resolve_executable(
            os.environ.get("BUZZ_ACP_MEDIA_ADAPTER_COMMAND", ""),
            "BUZZ_ACP_MEDIA_ADAPTER_COMMAND",
        )
        if Path(adapter).resolve() == Path(sys.argv[0]).resolve():
            raise MediaProxyError("media proxy adapter command recurses to itself")
        buzz_cli = _resolve_executable(
            os.environ.get("BUZZ_ACP_MEDIA_BUZZ_CLI", "buzz"),
            "BUZZ_ACP_MEDIA_BUZZ_CLI",
        )
        return proxy(adapter, arguments, buzz_cli=buzz_cli)
    except MediaProxyError as error:
        print(f"buzz-acp media proxy: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
