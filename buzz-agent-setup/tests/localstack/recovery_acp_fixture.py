#!/usr/bin/env python3
"""L3 external ACP boundary double. Never used for L4 or production Agents."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
from recovery_wire_oracle import capture

root = Path(sys.argv[1])
output_lock = threading.Lock()
cancelled = {}  # session -> number of session/cancel notifications received


def emit(value):
    with output_lock:
        print(json.dumps(value), flush=True)


def reply(request, result):
    emit({"jsonrpc": "2.0", "id": request["id"], "result": result})


def checkpoint(path, letter):
    with path.open("a") as stream:
        stream.write(letter + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def dispatch(slot, session, phase, before, **extra):
    """Append-only execution log: which provider/native process ran what, when."""
    record = dict(slot=slot, session=session, phase=phase, checkpoint_before=before,
                  pid=os.getpid(), native_pid=os.getppid(), t=time.time(), **extra)
    with output_lock, (root / "acp-dispatch.jsonl").open("a") as stream:
        stream.write(json.dumps(record) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def send(slot, content):
    route = json.loads((root / (slot + ".route.json")).read_text())
    env = json.loads((root / "agent-cli-env.json").read_text())
    return subprocess.run([route["cli"], "messages", "send", "--channel", route["channel"],
                           "--reply-to", route["root"], "--content", content],
                          env=env, capture_output=True, timeout=20, check=False).returncode == 0


def hold(request, session, slot, baseline, seconds=300):
    """Stay active until THIS prompt is cancelled; a real process kill loses this worker."""
    for _ in range(int(seconds * 10)):
        if cancelled.get(session, 0) > baseline:
            if slot.startswith("slowcancel-"):
                # Widen the window between the durable cancel and ACP stop so a
                # queued start/steer can race the late cancelled completion.
                time.sleep(3)
            dispatch(slot, session, "end", None, stop="cancelled")
            reply(request, {"stopReason": "cancelled"})
            return
        time.sleep(0.1)
    dispatch(slot, session, "end", None, stop="max_turn_requests")
    reply(request, {"stopReason": "max_turn_requests"})


def finish(request, session, slot, stop):
    dispatch(slot, session, "end", None, stop=stop)
    reply(request, {"stopReason": stop})


def prompt(request, baseline):
    capture(root / "acp-wire.jsonl", request)
    session = request["params"]["sessionId"]
    text = json.dumps(request["params"]["prompt"])
    # Earlier Thread messages are context, not the task: a new request after a
    # cancel in the same Thread must not be mistaken for the cancelled work.
    task_text = re.sub(r"<thread-context.*?</thread-context>", "", text)
    slots = set(re.findall(r"RECOVERY-L3:([a-z0-9-]+)", task_text))
    if len(slots) != 1:
        reply(request, {"stopReason": "refusal"})
        return
    slot = next(iter(slots))
    path = root / (slot + ".checkpoint")
    old = path.read_text() if path.exists() else ""
    dispatch(slot, session, "start", old)
    if slot.startswith(("waiting-human-", "waiting-approval-")):
        if old:
            # A waiting control must never be executed again; make it visible.
            checkpoint(path, "X")
            finish(request, session, slot, "refusal")
            return
        checkpoint(path, "A")
        question = ("QUESTION " + slot + ": 需要你确认后才能继续，请回复。" if slot.startswith("waiting-human-")
                    else "APPROVAL-REQUEST " + slot + ": 需要 owner 审批后才能执行。")
        finish(request, session, slot, "end_turn" if send(slot, question) else "refusal")
        return
    if not old:
        checkpoint(path, "A")
        if not slot.startswith("completed"):
            # First execution stays active until explicitly interrupted. A real
            # process restart loses this worker; the next prompt sees durable A.
            hold(request, session, slot, baseline)
            return
    if slot.startswith("stuck-"):
        hold(request, session, slot, baseline, seconds=3600)  # a target that never finishes
        return
    if slot.startswith("hang-") and path.read_text() == "A\n":
        resumed = root / (slot + ".resumes")
        attempts = resumed.read_text().split() if resumed.exists() else []
        with resumed.open("a") as stream:
            stream.write(str(os.getppid()) + "\n")
        if not attempts:
            hold(request, session, slot, baseline)  # admitted resume still running at the next crash
            return
    if path.read_text() == "A\n":
        checkpoint(path, "B")
    finish(request, session, slot, "end_turn" if send(slot, "DONE " + slot) else "refusal")


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        reply(request, {"protocolVersion": 1, "agentCapabilities": {"loadSession": False},
                        "authMethods": [], "agentInfo": {"name": "recovery-l3-fixture", "version": "1"}})
    elif method == "session/new":
        reply(request, {"sessionId": str(uuid.uuid4())})
    elif method == "session/prompt":
        # Read the cancel count on the stdin thread, before any later notification.
        threading.Thread(target=prompt, args=(request, cancelled.get(request["params"]["sessionId"], 0)),
                         daemon=True).start()
    elif method == "session/cancel":
        cancelled[request["params"]["sessionId"]] = cancelled.get(request["params"]["sessionId"], 0) + 1
        if "id" in request:
            reply(request, {})
    elif "id" in request:
        emit({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "fixture method unsupported"}})
