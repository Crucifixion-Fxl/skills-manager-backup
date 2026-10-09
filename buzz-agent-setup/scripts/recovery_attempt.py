"""Cross-language AttemptID: compact JSON [origin, agent, generation, h, root, WorkIDs]."""
import hashlib
import json
import uuid

from recovery_origin import canonical_origin
from recovery_authority import HEX64


def attempt_id(relay, agent, generation, channel, root, work):
    if (any(not isinstance(key, str) or not HEX64.fullmatch(key) for key in (agent, root))
            or any(not isinstance(key, str) or str(uuid.UUID(key)) != key for key in (generation, channel))
            or not isinstance(work, list) or not 1 <= len(work) <= 256
            or any(not isinstance(key, str) or not HEX64.fullmatch(key) for key in work)
            or work != sorted(set(work))):
        raise ValueError("invalid recovery attempt binding")
    parts = [canonical_origin(relay), agent, generation, channel, root, work]
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()
