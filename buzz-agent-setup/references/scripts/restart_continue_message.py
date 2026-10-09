"""Build the visible Buzz mention used after an Agent restart.

The caller supplies a trusted Channel, Thread root, and Agent identity only
after confirming the restarted runtime is ready. This module does not discover
in-flight Threads or perform a restart.
"""

import re
import uuid


_AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z")
_HEX_ID = re.compile(r"[0-9a-f]{64}\Z")


def build_continue_args(*, channel: str, root: str, agent_name: str, agent_pubkey: str) -> list[str]:
    """Return Buzz CLI arguments for a visible, real same-Thread mention."""
    try:
        canonical_channel = str(uuid.UUID(channel))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("channel must be a canonical UUID") from exc
    if canonical_channel != channel:
        raise ValueError("channel must be a canonical UUID")
    if not _HEX_ID.fullmatch(root):
        raise ValueError("root must be a 64-character lowercase event ID")
    if not _HEX_ID.fullmatch(agent_pubkey):
        raise ValueError("agent_pubkey must be a 64-character lowercase pubkey")
    if not _AGENT_NAME.fullmatch(agent_name):
        raise ValueError("agent_name must be a safe Buzz Agent name")
    return [
        "messages", "send", "--channel", channel,
        "--reply-to", root,
        "--mention", agent_pubkey,
        "--content", f"@{agent_name} continue",
    ]
