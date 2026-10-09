#!/usr/bin/env python3
"""L3 external ACP boundary double for hostd probes: answers the ACP handshake and records each prompt.
Never used for L4 or production agents. argv[1] = directory for prompts.jsonl."""
import json, sys, time, uuid
from pathlib import Path
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
def emit(v): print(json.dumps(v), flush=True)
for line in sys.stdin:
    try: req = json.loads(line)
    except ValueError: continue
    m, rid = req.get("method"), req.get("id")
    if m == "initialize":
        emit({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": False, "promptCapabilities": {"image": True}}, "authMethods": []}})
    elif m == "session/new":
        emit({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": str(uuid.uuid4())}})
    elif m == "session/prompt":
        with (out / "prompts.jsonl").open("a") as f:
            f.write(json.dumps({"t": time.time(), "prompt": req["params"].get("prompt")}) + "\n")
        emit({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
    elif rid is not None:
        emit({"jsonrpc": "2.0", "id": rid, "result": {}})
