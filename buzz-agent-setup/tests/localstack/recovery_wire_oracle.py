"""Bounded external ACP capture and exact recovery-scope L3 observations."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

LIMIT = 32 * 1024 * 1024


def capture(path, request):
    encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_RDWR | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "ab") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        if os.fstat(output.fileno()).st_size + len(encoded) > LIMIT:
            raise ValueError("ACP observation capacity exceeded")
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())


def read(path):
    if not Path(path).exists():
        return []
    with Path(path).open("rb") as source:
        fcntl.flock(source, fcntl.LOCK_SH)
        raw = source.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("ACP observation capacity exceeded")
    return [json.loads(line) for line in raw.splitlines()]


def observe(path, *, event_id, actor, owner, channel, root, slot, other_slot, agent_name):
    """None means no prompt yet; a seen but wrong prompt fails immediately."""
    for request in read(path):
        text = "\n".join(block.get("text", "") for block in request["params"]["prompt"])
        if root not in text:
            continue
        # Identify only continuation dispatches, not the first original task.
        # Old native builds send the owner event; scoped builds use the manifest.
        if "<recovery-scope>" not in text and f"Content: @{agent_name} continue" not in text:
            continue
        if f"RECOVERY-L3:{slot}" not in text:
            continue
        assert f"RECOVERY-L3:{other_slot}" not in text, "unselected source leaked into ACP execution context"
        assert f"hex: {owner}" not in text, "owner scheduling identity became execution authority"
        assert f"Content: @{agent_name} continue" not in text, "owner continue became task body"
        match = re.search(r"<recovery-scope>\n[^\n]+\n(\[[^\n]+\])\n</recovery-scope>", text)
        assert match, "missing runtime recovery execution scope"
        manifest = json.loads(match[1])
        assert manifest == [{"work_id": event_id, "signed_author": actor,
                             "effective_requester": actor, "channel": channel,
                             "thread_root": root, "mode": "resume"}], "original authority/subset changed"
        assert f"hex: {actor}" in text, "original signed requester missing from task"
        return {"work_id": event_id, "requester": actor, "session_id": request["params"]["sessionId"],
                "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(), "other_source_absent": True}
    return None
