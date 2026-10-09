#!/usr/bin/env python3
"""L3 black-box recovery: real local relay, native runtime, systemd, public controller CLI.

Only the external ACP provider is deterministic. This is NOT an L4 receipt.
Only the controller sends valid owner continue messages. The optional adversarial
sender control injects an explicitly untrusted member-signed request, never a
manual owner recovery or a substitute for automatic delivery.
"""
import argparse
import hashlib
import html
import json
import os
import re
from collections import Counter
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
import stack
import recovery_controller
from recovery_relay import RecoveryRelay, wire
from recovery_wire_oracle import observe as observe_execution, read as read_wire
import recovery_matrix
import recovery_scenarios
from recovery_ack_proxy import AckDropProxy
from types import SimpleNamespace

SCENARIOS = ("default", *recovery_scenarios.SCENARIOS)
LAST_EVIDENCE = None
MATRIX = (("default", dict(waiting_controls=True)),
          *((name, dict(waiting_controls=True)) for name in recovery_scenarios.SCENARIOS))


def private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
    return path


def wait_for(label, predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.25)
    raise RuntimeError("timed out: " + label)


def systemctl(*args, check=True):
    return subprocess.run(["/usr/bin/systemctl", "--user", *args], capture_output=True, text=True,
                          timeout=60, check=check)


def provider_children(pid):
    """Inspect actual child processes before declaring the provider asleep."""
    # Linux children files belong to individual threads, not the whole process.
    # Tokio can spawn every provider from a worker with an empty leader list.
    tasks = list(Path(f"/proc/{pid}/task").iterdir())
    if not tasks:
        raise RuntimeError("runtime thread inventory unavailable")
    return {int(value) for task in tasks for value in (task / "children").read_text().split()}


def replace_runtime(mode, unit, before, current, *, observations=None):
    """Drive the real service replacement; shared by both black-box scenarios."""
    if mode == "planned":
        command = systemctl("restart", unit)
    else:
        # systemd can kill the main process but return an auxiliary-process error.
        # Neither exit 0 nor a nonzero exit proves what happened to that process.
        command = systemctl("kill", "--kill-whom=all", "--signal=SIGKILL", unit, check=False)
    evidence = {"mode": mode, "unit": unit, "command_returncode": command.returncode,
                "command_diagnostic": stack.redact(command.stderr or "")[:300],
                "old_generation": before["generation"], "old_pid": before["pid"],
                "old_process_dead_verified": False, "new_process_live_verified": False}
    if observations is not None:
        observations.append(evidence)
    # Check boot ID and PID birth, not just PID absence or a changed journal.
    # Unknown / unreadable process state propagates; it is never treated as dead.
    wait_for(mode + " old process death", lambda: recovery_controller.process_live(before) is False)
    evidence["old_process_dead_verified"] = True
    after = wait_for(mode + " replacement runtime", lambda: (
        value if (value := current()) and value["generation"] != before["generation"]
        and recovery_controller.process_live(value) is True else None))
    evidence.update(new_generation=after["generation"], new_pid=after["pid"],
                    new_process_live_verified=True)
    return after


def runtime_binary(source, work):
    """Pin a private executable inode for every generation of this test run."""
    target = work / "buzz-acp"
    with source.open("rb") as incoming:
        before = os.fstat(incoming.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("L3 binary must be a regular file")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o500)
        with os.fdopen(fd, "wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        after = os.fstat(incoming.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("L3 binary changed during staging")
    return target


def completed_source_binding(current, event_id):
    """Completion evidence is durable even if the provider subsequently sleeps."""
    snapshot = current(require_ready=False)
    assert snapshot is not None and recovery_controller.process_live(snapshot)
    assert snapshot["receipts"].get(event_id) == "completed"
    return snapshot["input_sources"][event_id]


def native_denial_counts(unit, pid):
    result = subprocess.run(["/usr/bin/journalctl", "--user", "-u", unit, f"_PID={pid}", "-o", "cat", "--no-pager"],
                            capture_output=True, text=True, timeout=10, check=True)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    return Counter(match for line in plain.splitlines()
                   if "cannot durably admit event; task was not executed" in line and "error=recovery_source_not_member" in line
                   for match in re.findall(r"input_id=([0-9a-f]{64})(?:\s|$)", line))


def deferred_rechecks(snapshot, generation, deliveries, denials):
    """Observe native denial pacing, not merely the initial visible warning."""
    if snapshot is None or snapshot["generation"] != generation or snapshot["active"]:
        return None
    for input_id in deliveries:
        value = snapshot.get("deferred", {}).get(input_id, {})
        if (snapshot.get("receipts", {}).get(input_id) != "blocked" or value.get("checks", 0) < 1
                or not value.get("notice") or value["notice"].get("delivered") is not True
                or denials.get(input_id, 0) < 2):
            return None
    return snapshot


def run(binary, *, lazy_pool=False, idle_pool_sleep=0, expected_schema=10, source_membership=False, forged_sender=False,
        receive_membership=False, mixed_sources=False, receipt_timeout=False, relay_outage=False, shutdown_outage=False,
        systemd_ticks=False, installed_timer=False, waiting_controls=False, scenario="default"):
    if scenario not in SCENARIOS:
        raise ValueError("unknown L3 scenario")
    special = (lazy_pool, idle_pool_sleep, source_membership, forged_sender, receive_membership, mixed_sources,
               receipt_timeout, relay_outage, shutdown_outage, systemd_ticks)
    if (installed_timer or scenario != "default") and any(special):
        raise ValueError("installed-timer and matrix scenarios run on the plain v9 stack only")
    if (installed_timer or scenario != "default" or waiting_controls) and expected_schema < 9:
        raise ValueError("matrix scenarios require the v9 protocol")
    if idle_pool_sleep and not lazy_pool:
        raise ValueError("idle pool scenario requires lazy pool")
    if receive_membership and (not source_membership or idle_pool_sleep):
        raise ValueError("receive-time race requires source membership and no idle-sleep scenario")
    if mixed_sources and (not source_membership or idle_pool_sleep or expected_schema < 8):
        raise ValueError("mixed execution requires v8 source membership and no idle-sleep scenario")
    if receipt_timeout and (receive_membership or idle_pool_sleep or expected_schema < 8):
        raise ValueError("receipt timeout uses its own frozen-receiver scenario on v8")
    if relay_outage and (receive_membership or receipt_timeout or idle_pool_sleep or expected_schema < 8):
        raise ValueError("relay outage uses its own real transport failure scenario on v8")
    global LAST_EVIDENCE
    work = Path(tempfile.mkdtemp(prefix="buzz-recovery-l3-"))
    LAST_EVIDENCE = work
    print(json.dumps({"level": "L3", "evidence_dir": str(work)}), flush=True)
    binary = runtime_binary(binary, work)
    probe = subprocess.run([str(binary), "recovery-schema"], env={}, capture_output=True, timeout=10, check=True)
    capability = json.loads(probe.stdout)
    assert capability["version"] == expected_schema and capability["cancellation_tombstones"] is True
    if expected_schema >= 3:
        assert capability["prewarm_signal"] == "SIGUSR1"
    if expected_schema >= 4:
        assert capability["runtime_policy_snapshot"] is True
    if expected_schema >= 5:
        assert capability["original_source_bindings"] is True
    if expected_schema >= 6:
        assert capability["deferred_same_attempt"] is True
    if expected_schema >= 7:
        assert capability["durable_denial_notices"] is True
    if expected_schema >= 8:
        assert capability["exact_attempt_binding"] is True
    if expected_schema >= 9:
        assert capability["durable_shutdown_notices"] is True
    if expected_schema >= 10:
        assert capability["durable_storage_feedback"] is True and capability["bounded_terminal_retention"] is True
    label = "brl3-" + hashlib.sha256(str(work).encode()).hexdigest()[:10]
    unit = "buzz-" + label + ".service"
    stack.STATE_DIR, stack.SECRETS_DIR, stack.LOGS_DIR = work / "stack", work / "secrets", work / "logs"
    stack.STACK_ID = label
    stack.LABEL_KEY = "ai.addx.buzz-recovery-localstack"
    stack.NAMES = {key: label + "-" + key for key in ("relay", "postgres", "redis", "network")}
    for directory in (stack.STATE_DIR, stack.SECRETS_DIR, stack.LOGS_DIR, work / "runtime", work / "controller", work / "checkpoints"):
        directory.mkdir(mode=0o700)
    checks = []
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True, check=True).stdout.strip()
    report = {"level": "L3", "external_double": "ACP provider only", "manual_continue_sent": False,
              "evidence_dir": str(work), "started_at": time.time(),
              "lazy_pool": lazy_pool,
              "idle_pool_sleep": idle_pool_sleep,
              "source_membership": source_membership,
              "forged_sender": forged_sender,
              "receive_membership": receive_membership,
              "mixed_sources": mixed_sources,
              "receipt_timeout": receipt_timeout,
              "relay_outage": relay_outage,
              "shutdown_outage": shutdown_outage,
              "controller_driver": ("installed-timer" if installed_timer else
                                    "owned-systemd-oneshots" if systemd_ticks else "direct-cli"),
              "scenario": scenario, "waiting_controls": waiting_controls,
              "expected_schema": expected_schema,
              "runtime_binary": str(binary),
              "source_revision": revision, "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "checks": checks, "replacement_observations": []}
    report["file_sha256"] = {str(path.relative_to(HERE.parents[1])): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in [Path(__file__), HERE / "recovery_acp_fixture.py", HERE / "recovery_wire_oracle.py",
                                          HERE / "recovery_systemd_tick.py", HERE / "recovery_timer.py",
                                          HERE / "recovery_matrix.py", HERE / "recovery_scenarios.py",
                                          HERE / "recovery_ack_proxy.py", HERE / "stack.py",
                                          *(HERE.parents[1] / "scripts").glob("*recovery*.py")]}
    timer = proxy = None
    direct_calls = []
    drop_rule = dict(root=None, budget=0)
    try:
        for image in (stack.DEFAULT_RELAY_IMAGE, stack.PG_IMAGE, stack.REDIS_IMAGE):
            stack.ensure_image(image)
        owner, relay_id, agent = (stack.new_identity(name) for name in ("owner", "relay", "role"))
        agent["auth_tag"] = stack.attest(owner, agent)
        if scenario == "lost-ack":
            def should_drop(event):
                roots = [t[1] for t in event.get("tags", []) if t[:1] == ["e"] and t[-1:] == ["root"]]
                if (drop_rule["budget"] > 0 and event.get("pubkey") == owner["pubkey"]
                        and event.get("content") == "@" + label + " continue" and roots == [drop_rule["root"]]):
                    drop_rule["budget"] -= 1
                    return True
                return False
            proxy = AckDropProxy(should_drop)
            relay = stack.start_relay_stack(stack.DEFAULT_RELAY_IMAGE, owner, relay_id, public_port=proxy.port)
            proxy.start(relay["upstream_port"])
            report["fault_proxy"] = {"public_port": proxy.port, "upstream_port": relay["upstream_port"]}
        else:
            relay = stack.start_relay_stack(stack.DEFAULT_RELAY_IMAGE, owner, relay_id)
        transport = RecoveryRelay(relay["http_url"], owner["secret"], relay_id["pubkey"])
        source = stack.new_identity("requester") if source_membership else owner
        source_transport = transport
        if source_membership:
            stack.docker("exec", stack.NAMES["relay"], "buzz-admin", "add-member", "--pubkey", source["pubkey"])
            source_transport = RecoveryRelay(relay["http_url"], source["secret"], relay_id["pubkey"])
        intruder = stack.new_identity("forged-recovery-member") if forged_sender else None
        if intruder:
            stack.docker("exec", stack.NAMES["relay"], "buzz-admin", "add-member", "--pubkey", intruder["pubkey"])
        other = stack.new_identity("queued-requester") if mixed_sources else None
        other_transport = None
        if other:
            stack.docker("exec", stack.NAMES["relay"], "buzz-admin", "add-member", "--pubkey", other["pubkey"])
            other_transport = RecoveryRelay(relay["http_url"], other["secret"], relay_id["pubkey"])
        stack.docker("exec", stack.NAMES["relay"], "buzz-admin", "add-member", "--pubkey", agent["pubkey"])
        stack.buzz(owner, "users", "set-profile", "--name", "recovery-l3-owner", relay_http=relay["http_url"])
        stack.buzz(agent, "users", "set-profile", "--name", label, relay_http=relay["http_url"])
        channels = []
        for n in range(3 if scenario == "stuck-target" else 2):
            created = json.loads(stack.buzz(owner, "channels", "create", "--name", f"{label}-{n}", "--type", "stream",
                                           "--visibility", "private", relay_http=relay["http_url"]).stdout)
            channels.append(created["channel_id"])
            stack.buzz(owner, "channels", "add-member", "--channel", channels[-1], "--pubkey", agent["pubkey"], "--role", "bot", relay_http=relay["http_url"])
            if source_membership:
                stack.buzz(owner, "channels", "add-member", "--channel", channels[-1], "--pubkey", source["pubkey"], "--role", "member", relay_http=relay["http_url"])
            if intruder:
                stack.buzz(owner, "channels", "add-member", "--channel", channels[-1], "--pubkey", intruder["pubkey"], "--role", "member", relay_http=relay["http_url"])
            if other:
                stack.buzz(owner, "channels", "add-member", "--channel", channels[-1], "--pubkey", other["pubkey"], "--role", "member", relay_http=relay["http_url"])
        respond_to = "anyone" if source_membership or forged_sender else "owner-only"
        pool = 12 if scenario != "default" or waiting_controls else 6
        report["provider_pool"] = pool
        wire.publish_event(relay["http_url"], owner["secret"], 30177, [["d", agent["pubkey"]]],
                           json.dumps({"name": label, "respond_to": respond_to, "parallelism": pool}), wire._http_get, transport.now())
        checkpoint_dir = work / "checkpoints"
        cli_env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "BUZZ_RELAY_URL": relay["http_url"],
                   "BUZZ_PRIVATE_KEY": agent["secret"], "BUZZ_AUTH_TAG": agent["auth_tag"]}
        private(checkpoint_dir / "agent-cli-env.json", json.dumps(cli_env))
        env = {**cli_env, "BUZZ_RELAY_URL": relay["ws_url"], "BUZZ_ACP_AGENT_OWNER": owner["pubkey"],
               "BUZZ_ACP_AGENT_COMMAND": "/usr/bin/python3", "BUZZ_ACP_AGENT_ARGS": f"{HERE / 'recovery_acp_fixture.py'},{checkpoint_dir}",
               "BUZZ_ACP_AGENTS": str(pool), "BUZZ_ACP_CHANNELS": ",".join(channels), "BUZZ_ACP_SESSION_POLICY": "thread",
               "BUZZ_ACP_RESPOND_TO": respond_to, "BUZZ_ACP_MULTIPLE_EVENT_HANDLING": "queue",
               "BUZZ_ACP_IDLE_TIMEOUT": "120", "BUZZ_ACP_MAX_TURN_DURATION": "600",
               "BUZZ_ACP_RECOVERY_DIR": str(work / "runtime"), "BUZZ_ACP_RECOVERY_REVISION": revision,
               "BUZZ_ACP_BINARY_SHA256": report["binary_sha256"]}
        if os.environ.get("BUZZ_L3_NATIVE_LOG"):
            # Diagnostics only (e.g. buzz_acp=debug); never changes behavior under test.
            env["RUST_LOG"] = os.environ["BUZZ_L3_NATIVE_LOG"]
            report["native_log_filter"] = env["RUST_LOG"]
        if lazy_pool:
            env["BUZZ_ACP_LAZY_POOL"] = "true"
            env["BUZZ_ACP_IDLE_POOL_SLEEP"] = str(idle_pool_sleep)
        # EnvironmentFile uses systemd quoting, not shell evaluation.
        agent_env = private(work / "agent.env", "".join(f"{key}={json.dumps(value)}\n" for key, value in env.items()))
        owner_env = private(work / "owner.env", f"BUZZ_PRIVATE_KEY={owner['secret']}\nBUZZ_RELAY_URL={relay['http_url']}\n")
        config = {"version": 1, "owner_pubkey": owner["pubkey"], "owner_env_file": str(owner_env),
                  "relay_url": relay["ws_url"], "relay_pubkey": relay_id["pubkey"], "state_dir": str(work / "controller"),
                  "agents": [{"name": label, "pubkey": agent["pubkey"], "env_file": str(agent_env), "unit": unit,
                              "journal_dir": str(work / "runtime"), "revision": revision, "binary_sha256": report["binary_sha256"]}]}
        config_path = private(work / "controller.json", json.dumps(config))
        controller = HERE.parents[1] / "scripts/recovery_controller.py"
        subprocess.run(["/usr/bin/systemd-run", "--user", "--unit=" + unit, "--property=Type=exec",
                        "--property=EnvironmentFile=" + str(agent_env), "--property=WorkingDirectory=" + str(work),
                        "--property=KillMode=control-group", "--property=Restart=on-failure", "--property=RestartSec=2s",
                        "--property=TimeoutStopSec=45s", str(binary)], capture_output=True, text=True, check=True, timeout=15)

        def current(require_ready=True):
            pid = int(systemctl("show", unit, "--property=MainPID", "--value").stdout.strip() or "0")
            for path in (work / "runtime").glob("*.json"):
                value = json.loads(path.read_text())
                if value["pid"] == pid and (not require_ready or (
                        value["phase"] == "ready" and set(value["channels"]) == set(channels))):
                    return value
            return None

        initial = wait_for("initial runtime journal", lambda: current(require_ready=not lazy_pool))
        if expected_schema >= 4:
            assert initial["runtime_policy"] == dict(owner=owner["pubkey"], respond_to=respond_to, allowlist=[])
            report["runtime_policy_snapshot_verified"] = True
        if lazy_pool:
            assert initial["phase"] == "starting" and initial["active"] == {}, "empty lazy Agent must not claim readiness"
        report["channels"] = channels
        report["agent_pubkey"] = agent["pubkey"]
        if installed_timer:
            # The installer's own unit text, a unique test name and this run's
            # private config. From here on only timer ticks run the controller.
            from recovery_timer import InstalledTimer
            timer = InstalledTimer("buzz-recovery-l3-" + label.split("-", 1)[1], release=HERE.parents[1],
                                   config_path=config_path, state_dir=str(work / "controller"))
            report["installed_timer"] = timer.install()
        routes = {}
        original_inputs = {}

        def task(slot, channel):
            seed = transport.sign("Thread RECOVERY-L3:" + slot, [["h", channel]])
            transport.publish(seed)
            event = source_transport.sign(f"@{label} RECOVERY-L3:{slot}", [["h", channel], ["e", seed["id"], "", "root"],
                                                                     ["e", seed["id"], "", "reply"], ["p", agent["pubkey"]]])
            private(checkpoint_dir / (slot + ".route.json"), json.dumps({"cli": str(stack.BUZZ_CLI), "channel": channel, "root": seed["id"]}))
            source_transport.publish(event)
            routes[slot] = seed
            original_inputs[slot] = event["id"]

        def checkpoint_is(slot, expected):
            path = checkpoint_dir / (slot + ".checkpoint")
            return path.exists() and path.read_text() == expected

        def execution_scope(slot, other_slot, actor):
            seed = routes[slot]
            return observe_execution(checkpoint_dir / "acp-wire.jsonl", event_id=original_inputs[slot],
                                     actor=actor["pubkey"], owner=owner["pubkey"], channel=seed["tags"][0][1],
                                     root=seed["id"], slot=slot, other_slot=other_slot, agent_name=label)

        def wait_idle():
            before_idle = current()
            assert before_idle is not None, "must observe actual readiness before idle"
            live_children = provider_children(before_idle["pid"])
            assert live_children, "must observe live providers before waiting for idle"
            wait_for("provider actually stopped while harness is live", lambda: (
                recovery_controller.process_live(before_idle) and not provider_children(before_idle["pid"])))
            asleep = current(require_ready=False)
            assert asleep["generation"] == before_idle["generation"]
            assert asleep["phase"] != "ready", "sleeping provider still claims ready"
            report.setdefault("provider_idle_observations", []).append({
                "generation": asleep["generation"], "observed_provider_pids": sorted(live_children),
                "phase_after_all_providers_exited": asleep["phase"]})
            return asleep

        def controller_tick(name):
            invocation = None
            if timer is not None:
                done, observed = timer.next_tick()
                invocation = observed["invocation_id"]
            elif systemd_ticks:
                from recovery_systemd_tick import run_tick
                done = run_tick(controller, config_path, observations=report.setdefault("systemd_tick_observations", []))
            else:
                direct_calls.append(name)
                done = subprocess.run(["/usr/bin/python3", "-I", str(controller), "--config", str(config_path)],
                                      env={"PATH": "/usr/bin:/bin", "XDG_RUNTIME_DIR": os.environ["XDG_RUNTIME_DIR"]},
                                      capture_output=True, text=True, timeout=120)
            private(work / (name + "-controller.json"), done.stdout)
            value = json.loads(done.stdout)["agents"][label]
            assert 0 <= value["scheduled"] <= 2, "production per-Agent scheduling bound exceeded"
            assert value["continued"] <= value["scheduled"]
            report.setdefault("controller_schedule_observations", []).append(
                {"tick": name, **value, **({"invocation_id": invocation} if invocation else {})})
            return done.returncode, value

        def hold(reason):
            if timer is not None:
                timer.pause(reason)

        def release():
            if timer is not None:
                timer.resume()

        def barrier():
            if timer is not None:
                timer.barrier()

        def caught_up():
            return timer is None or timer.caught_up()

        def controller_sweep(name):
            # Four fixture Threads need two independent public CLI ticks under
            # the production two-per-Agent limit. Never override that limit.
            code, first = controller_tick(name + "-tick-0")
            if first.get("prewarming"):
                return code, first  # observe actual ready before another tick
            later_code, later = controller_tick(name + "-tick-1")
            return max(code, later_code), dict(continued=first["continued"] + later["continued"],
                errors=first["errors"] + later["errors"],
                prewarming=bool(first.get("prewarming") or later.get("prewarming")))

        def outage_then_recover(mode, slots):
            observed = current()
            assert observed is not None and observed["active"] == {}
            failed_ticks, restored_ticks = [], []
            stack.docker("stop", "--time", "1", stack.NAMES["relay"])
            try:
                def retry_wait_observed():
                    code, value = controller_tick(mode + "-relay-offline-" + str(len(failed_ticks)))
                    failed_ticks.append({"code": code, **value})
                    assert code == 1 and value["continued"] == 0
                    assert set(value["errors"]) <= {"recovery_delivery_unverified"}
                    assert value["retry_reasons"] == ["recovery_delivery_unverified"]
                    assert all(checkpoint_is(slot, "A\n") for slot in slots)
                    return (value["scheduled"] == 0 and value["retry_pending"] == 4
                            and value["next_retry_at"] > time.time())
                wait_for("real offline controller enters durable retry wait", retry_wait_observed, timeout=25)
            finally:
                stack.docker("start", stack.NAMES["relay"])
            def relay_ready():
                try:
                    return stack.http("GET", relay["health_url"], timeout=3)[0] == 200
                except (OSError, ValueError, stack.StackError):
                    return False
            wait_for("same isolated Relay becomes ready", relay_ready, timeout=45)
            def automatic_restore():
                code, value = controller_tick(mode + "-relay-restored-" + str(len(restored_ticks)))
                restored_ticks.append({"code": code, **value})
                assert set(value["errors"]) <= ({"source_not_member"} if mixed_sources else set())
                assert current(require_ready=False)["generation"] == observed["generation"]
                continued = sum(tick["continued"] for tick in restored_ticks)
                assert continued <= 4, "network recovery duplicated a delivery"
                return continued == 4
            wait_for("automatic four-Thread delivery after real Relay restoration", automatic_restore, timeout=90)
            report.setdefault("relay_outage_observations", []).append({
                "scenario": mode, "generation": observed["generation"], "offline_ticks": failed_ticks,
                "restored_ticks": restored_ticks, "manual_continue": False, "published": 4})

        def hold_receiver_until_slow_notice(mode, slots):
            observed = current()
            assert observed is not None and recovery_controller.process_live(observed)
            descriptor = os.pidfd_open(observed["pid"], 0)
            ticks = []
            try:
                assert recovery_controller.process_live(observed)
                signal.pidfd_send_signal(descriptor, signal.SIGSTOP, None, 0)
                wait_for("verified receiver stopped before automatic publication", lambda: (
                    Path(f"/proc/{observed['pid']}/stat").read_text().rsplit(")", 1)[1].split()[0] == "T"))
                code, delivered = controller_sweep(mode + "-timeout-publication")
                assert code == int(mixed_sources) and delivered["continued"] == 4
                assert set(delivered["errors"]) == ({"source_not_member"} if mixed_sources else set())
                started = time.monotonic()
                def due():
                    code, value = controller_sweep(mode + "-timeout-wait-" + str(len(ticks)))
                    ticks.append({"code": code, **value})
                    assert value["continued"] == 0, "receipt wait re-signed or duplicated continue"
                    assert set(value["errors"]) <= ({"receipt_timeout", "source_not_member"} if mixed_sources else {"receipt_timeout"})
                    return value if value["errors"].count("receipt_timeout") == 4 else None
                wait_for("all four 120-second receipt warnings", due, timeout=180)
                wait_seconds = time.monotonic() - started
                assert current()["generation"] == observed["generation"] and current()["active"] == {}
                assert all(checkpoint_is(slot, "A\n") for slot in slots), "stopped receiver executed work"
                def notice_ids():
                    ids = []
                    for slot in slots:
                        seed = routes[slot]
                        events = transport.query([{"kinds": [9], "#h": [seed["tags"][0][1]], "#e": [seed["id"]], "limit": 100}])
                        notices = [event for event in events if "尚未确认接收" in event["content"]]
                        assert len(notices) == 1 and notices[0]["pubkey"] == owner["pubkey"]
                        assert "owner" in notices[0]["content"] and "无需" in notices[0]["content"]
                        assert not any(tag[0] == "p" for tag in notices[0]["tags"])
                        ids.append(notices[0]["id"])
                    return ids
                ids = notice_ids()
                due()
                assert notice_ids() == ids, "repeated receipt timeout duplicated its durable reason"
                report.setdefault("receipt_timeout_observations", []).append({
                    "scenario": mode, "generation": observed["generation"], "wait_seconds": wait_seconds,
                    "published": 4, "native_admissions_while_paused": 0, "notice_ids": ids,
                    "additional_continues": sum(tick["continued"] for tick in ticks)})
            finally:
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGCONT, None, 0)
                finally:
                    os.close(descriptor)

        if intruder:
            # Exercise the actual listener, not just the journal helper. An
            # ordinary member passes respond_to=anyone but cannot impersonate
            # the system scheduler. The Agent's signed denial proves it handled
            # this input; absence of a checkpoint alone would be inconclusive.
            slot, channel = "forged-sender-control", channels[0]
            seed = transport.sign("Thread RECOVERY-L3:" + slot, [["h", channel]])
            transport.publish(seed)
            private(checkpoint_dir / (slot + ".route.json"), json.dumps({"cli": str(stack.BUZZ_CLI), "channel": channel, "root": seed["id"]}))
            hostile = RecoveryRelay(relay["http_url"], intruder["secret"], relay_id["pubkey"])
            forged = hostile.sign(f"@{label} continue", [
                ["h", channel], ["e", seed["id"], "", "root"], ["e", seed["id"], "", "reply"],
                ["p", agent["pubkey"]], ["recovery", "f" * 64, initial["generation"]]])
            hostile.publish(forged)
            private(work / "forged-sender-event.json", json.dumps(forged))

            def sender_notices():
                events = transport.query([{"kinds": [9], "#h": [channel], "#e": [seed["id"]], "limit": 100}])
                return [e for e in events if e["pubkey"] == agent["pubkey"] and "发送身份" in e["content"]]

            notices = wait_for("native rejects member-signed recovery with actionable notice", sender_notices, timeout=30)
            assert len(notices) == 1 and "owner" in notices[0]["content"] and "磁盘" not in notices[0]["content"]
            assert not any(tag[0] == "p" for tag in notices[0]["tags"]), "denial must not wake the Agent"
            rejected_state = current(require_ready=False)
            assert forged["id"] not in rejected_state["receipts"] and rejected_state["active"] == {}
            assert not (checkpoint_dir / (slot + ".checkpoint")).exists(), "forged recovery reached ACP"
            private(work / "forged-sender-notices.json", json.dumps(notices, ensure_ascii=False))
            report["sender_authority_observation"] = {"respond_to": respond_to, "member_role": "member",
                "forged_event_id": forged["id"], "admitted": False, "checkpoint_created": False,
                "notice_event_id": notices[0]["id"], "notice_author": agent["pubkey"]}

        task("completed-control", channels[0])
        wait_for("completed control", lambda: checkpoint_is("completed-control", "A\nB\n"))
        wait_for("provider actually ready after first task", current)
        if idle_pool_sleep:
            wait_idle()
        task("cancelled-control", channels[1])
        wait_for("cancelled control admitted", lambda: checkpoint_is("cancelled-control", "A\n"))
        cancel_root = routes["cancelled-control"]["id"]
        transport.publish(transport.sign("!cancel", [["h", channels[1]], ["e", cancel_root, "", "root"],
                                                     ["e", cancel_root, "", "reply"], ["p", agent["pubkey"]]]))
        wait_for("control cancellations persisted", lambda: not current()["active"])

        def thread_events(slot):
            seed = routes[slot]
            return transport.query([{"kinds": [9], "#h": [seed["tags"][0][1]], "#e": [seed["id"]], "limit": 200}])

        def checkpoint_text(slot):
            path = checkpoint_dir / (slot + ".checkpoint")
            return path.read_text() if path.exists() else ""

        def dispatches(slot):
            path = checkpoint_dir / "acp-dispatch.jsonl"
            rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
            return [row for row in rows if row["slot"] == slot and row["phase"] == "start"]

        def view(slot):
            return dict(events=thread_events(slot), checkpoint=checkpoint_text(slot), dispatches=dispatches(slot))

        def journals():
            return {path.stem: json.loads(path.read_text()) for path in (work / "runtime").glob("*.json")}

        def followup(route_slot, text_slot):
            """Another owner request in an existing Thread (queued behind running work)."""
            seed = routes[route_slot]
            channel = seed["tags"][0][1]
            private(checkpoint_dir / (text_slot + ".route.json"), json.dumps({"cli": str(stack.BUZZ_CLI), "channel": channel, "root": seed["id"]}))
            event = source_transport.sign(f"@{label} RECOVERY-L3:{text_slot}", [["h", channel], ["e", seed["id"], "", "root"],
                                                                         ["e", seed["id"], "", "reply"], ["p", agent["pubkey"]]])
            source_transport.publish(event)
            routes.setdefault(text_slot, seed)
            original_inputs.setdefault(text_slot, event["id"])
            return event["id"]

        def cancel(slot):
            seed = routes[slot]
            transport.publish(transport.sign("!cancel", [["h", seed["tags"][0][1]], ["e", seed["id"], "", "root"],
                                                         ["e", seed["id"], "", "reply"], ["p", agent["pubkey"]]]))

        def set_role(channel, role):
            stack.buzz(owner, "channels", "add-member", "--channel", channel, "--pubkey", agent["pubkey"],
                       "--role", role, relay_http=relay["http_url"])

        def log_mentions(snapshot, event_ids):
            """Did the new generation's own log mention any of these event IDs (late arrival evidence)?"""
            logs = subprocess.run(["/usr/bin/journalctl", "--user", "-u", unit, f"_PID={snapshot['pid']}", "-o", "cat",
                                   "--no-pager"], capture_output=True, text=True, timeout=15).stdout
            return sorted(event_id for event_id in event_ids if event_id in logs)

        controls = {"completed-control": "completed", "cancelled-control": "cancelled"}
        if waiting_controls:
            # REC-005: turns that ENDED asking a person / an approver. Never a
            # deliberately suspended execution (acceptance.md happy path step 2).
            for slot, channel in (("waiting-human-control", channels[0]), ("waiting-approval-control", channels[1])):
                task(slot, channel)
                controls[slot] = slot.rsplit("-", 1)[0]
            wait_for("waiting controls asked and ended their turns", lambda: all(
                checkpoint_is(slot, "A\n") and any(e["pubkey"] == agent["pubkey"] for e in thread_events(slot))
                for slot in ("waiting-human-control", "waiting-approval-control")) and not current()["active"])
        control_baseline = {slot: thread_events(slot) for slot in controls}

        h = SimpleNamespace(task=task, followup=followup, cancel=cancel, set_role=set_role, view=view,
                            checkpoint=checkpoint_text, journals=journals, current=current, routes=routes,
                            original_inputs=original_inputs, channels=channels, owner=owner, agent=agent, label=label,
                            tick=controller_tick, hold=hold, release=release, wait_for=wait_for, proxy=proxy,
                            drop_rule=drop_rule, log_mentions=log_mentions, barrier=barrier, caught_up=caught_up,
                            replace=lambda mode, before: replace_runtime(mode, unit, before, current,
                                                                         observations=report["replacement_observations"]))

        for mode in (("planned", "crash") if scenario == "default" else ()):
            slots = [mode + "-" + str(n) for n in range(4)]
            for n, slot in enumerate(slots):
                task(slot, channels[n // 2])
            wait_for(mode + " four active checkpoints", lambda: all(checkpoint_is(slot, "A\n") for slot in slots))
            delayed = ["completed-delayed-" + slot for slot in slots] if mixed_sources else []
            for slot, delayed_slot in zip(slots, delayed):
                seed = routes[slot]
                channel = seed["tags"][0][1]
                event = other_transport.sign(f"@{label} RECOVERY-L3:{delayed_slot}", [
                    ["h", channel], ["e", seed["id"], "", "root"], ["e", seed["id"], "", "reply"], ["p", agent["pubkey"]]])
                private(checkpoint_dir / (delayed_slot + ".route.json"), json.dumps({"cli": str(stack.BUZZ_CLI), "channel": channel, "root": seed["id"]}))
                routes[delayed_slot], original_inputs[delayed_slot] = seed, event["id"]
                other_transport.publish(event)
            if mixed_sources:
                wait_for("all second-source requests durably queued", lambda: (
                    value if (value := current()) and all(value["receipts"].get(original_inputs[slot]) == "active" for slot in delayed) else None))
                assert all(not (checkpoint_dir / (slot + ".checkpoint")).exists() for slot in delayed)
                for channel in channels:
                    stack.buzz(owner, "channels", "remove-member", "--channel", channel,
                               "--pubkey", other["pubkey"], relay_http=relay["http_url"])
            before = current()
            assert before is not None and len(before["active"]) == 4 + len(delayed)
            assert set(original_inputs[slot] for slot in slots + delayed) == {event for ids in before["triggers"].values() for event in ids}, "original inputs must be durably admitted"
            hold(mode + "-replace-and-demote")  # no tick may race the staged role change
            if shutdown_outage and mode == "planned":
                # Real transport outage during the production stop hook; no
                # journal edits or manual continue are permitted in this test.
                stack.docker("stop", "--time", "1", stack.NAMES["relay"])
                # Keep the restart job attached to this transient unit; a
                # separate stop lets systemd garbage-collect its definition.
                systemctl("restart", unit)
                wait_for("offline shutdown old process dead", lambda: recovery_controller.process_live(before) is False)
                stopped = json.loads((work / "runtime" / (before["generation"] + ".json")).read_text())
                assert stopped["phase"] == "stopping"
                intents = stopped.get("shutdown_notices", {})
                assert len(intents) == 4, "offline shutdown lost durable per-Thread feedback intent"
                assert all(value["notice"] and value["notice"]["delivered"] is False for value in intents.values())
                stack.docker("start", stack.NAMES["relay"])
                def ready_again():
                    try:
                        return stack.http("GET", relay["health_url"], timeout=3)[0] == 200
                    except (OSError, ValueError, stack.StackError):
                        return False
                wait_for("Relay ready after offline shutdown", ready_again)
                after = wait_for("new runtime after offline shutdown", current)
                assert after["generation"] != before["generation"] and recovery_controller.process_live(after)
                report["replacement_observations"].append(dict(mode=mode, old_generation=before["generation"],
                    new_generation=after["generation"], old_pid=before["pid"], new_pid=after["pid"],
                    old_process_dead_verified=True, new_process_live_verified=True, offline_shutdown=True))
            else:
                after = replace_runtime(mode, unit, before, current, observations=report["replacement_observations"])
            if idle_pool_sleep:
                asleep = wait_idle()

            # REC-009: changing membership role must block the real controller,
            # even though the owner can still publish and the Agent is listed.
            for channel in channels:
                stack.buzz(owner, "channels", "add-member", "--channel", channel,
                           "--pubkey", agent["pubkey"], "--role", "member", relay_http=relay["http_url"])
            release()
            for tick in range(2):
                code, blocked = controller_sweep(mode + "-role-blocked-" + str(tick))
                assert code == 1 and blocked["continued"] == 0, "non-bot Agent was automatically continued"
                assert set(blocked["errors"]) == {"agent_not_bot"}
                assert blocked.get("prewarming") is not True, "blocked route requested prewarm"
            assert all(checkpoint_is(slot, "A\n") for slot in slots), "blocked recovery executed business work"
            for slot in slots:
                events = transport.query([{"kinds": [9], "#h": [routes[slot]["tags"][0][1]],
                                           "#e": [routes[slot]["id"]], "limit": 100}])
                notices = [e for e in events if e["pubkey"] == owner["pubkey"] and "不是机器人角色" in e["content"]]
                assert len(notices) == 1 and "owner" in notices[0]["content"], "missing or duplicated failure/remediation"
                assert not any(tag[0] == "p" for tag in notices[0]["tags"]), "failure notice must not wake the Agent"
            report.setdefault("authority_observations", []).append({
                "scenario": mode, "blocked_reason": "agent_not_bot", "ticks": 2,
                "continues": 0, "checkpoint": "A", "notices_per_thread": 1})
            hold(mode + "-restore-role")
            for channel in channels:
                stack.buzz(owner, "channels", "add-member", "--channel", channel,
                           "--pubkey", agent["pubkey"], "--role", "bot", relay_http=relay["http_url"])
            release()

            # REC-009 source authority: the owner's scheduling identity must
            # never replace the original requester's current membership.
            if source_membership:
                for channel in channels:
                    stack.buzz(owner, "channels", "remove-member", "--channel", channel,
                               "--pubkey", source["pubkey"], relay_http=relay["http_url"])
                for tick in range(2):
                    code, denied = controller_sweep(mode + "-source-blocked-" + str(tick))
                    assert code == 1 and denied["continued"] == 0, "revoked original requester was continued as owner"
                    assert set(denied["errors"]) == {"source_not_member"}
                assert all(checkpoint_is(slot, "A\n") for slot in slots), "revoked source executed business work"
                for slot in slots:
                    events = transport.query([{"kinds": [9], "#h": [routes[slot]["tags"][0][1]], "#e": [routes[slot]["id"]], "limit": 100}])
                    notices = [e for e in events if e["pubkey"] == owner["pubkey"] and "原请求" in e["content"]]
                    assert len(notices) == (2 if mixed_sources else 1) and all("owner" in notice["content"] for notice in notices), "source failure/remediation missing or duplicated"
                    expected_sources = [slot, "completed-delayed-" + slot] if mixed_sources else [slot]
                    assert all(any(original_inputs[name][:12] in notice["content"] for notice in notices) for name in expected_sources)
                    assert not any(tag[0] == "p" for tag in notices[0]["tags"])
                report.setdefault("authority_observations", []).append({
                    "scenario": mode, "blocked_reason": "source_not_member", "ticks": 2,
                    "continues": 0, "checkpoint": "A", "notices_per_thread": 2 if mixed_sources else 1})
                for channel in channels:
                    stack.buzz(owner, "channels", "add-member", "--channel", channel,
                               "--pubkey", source["pubkey"], "--role", "member", relay_http=relay["http_url"])

            if idle_pool_sleep:
                code, warming = controller_sweep(mode + "-prewarm")
                assert code == 1 and warming["continued"] == 0 and warming.get("prewarming") is True
                assert set(warming["errors"]) == {"runtime_not_ready"}, "unexpected prewarm error"
                assert all(checkpoint_is(slot, "A\n") for slot in slots), "prewarm executed old work"
                awake = wait_for(mode + " controller prewarm ready", current)
                assert awake["generation"] == asleep["generation"], "prewarm must not restart the Agent"
                assert awake["active"] == {}, "prewarm must not admit work"
                report.setdefault("idle_prewarm_observations", []).append({
                    "scenario": mode, "generation": awake["generation"], "idle_phase": asleep["phase"],
                    "ready_phase": awake["phase"], "continued_before_ready": warming["continued"]})
            if receive_membership:
                # Freeze only this test's verified native process. The real
                # controller publishes while the source still has authority;
                # revoke via the public Relay CLI before native can consume it.
                observed = current()
                assert observed is not None and recovery_controller.process_live(observed)
                descriptor = os.pidfd_open(observed["pid"], 0)
                try:
                    assert recovery_controller.process_live(observed)
                    signal.pidfd_send_signal(descriptor, signal.SIGSTOP, None, 0)
                    wait_for("exact native receiver stopped", lambda: (
                        Path(f"/proc/{observed['pid']}/stat").read_text().rsplit(")", 1)[1].split()[0] == "T"))
                    code, published = controller_sweep(mode + "-before-receive-revocation")
                    assert code == int(mixed_sources) and published["continued"] == 4, "controller must authorize selected sources before revocation"
                    assert set(published["errors"]) == ({"source_not_member"} if mixed_sources else set())
                    for channel in channels:
                        stack.buzz(owner, "channels", "remove-member", "--channel", channel,
                                   "--pubkey", source["pubkey"], relay_http=relay["http_url"])
                finally:
                    try:
                        signal.pidfd_send_signal(descriptor, signal.SIGCONT, None, 0)
                    finally:
                        os.close(descriptor)

                denied_ids = []
                for slot in slots:
                    def receiver_denial():
                        events = transport.query([{"kinds": [9], "#h": [routes[slot]["tags"][0][1]],
                                                   "#e": [routes[slot]["id"]], "limit": 100}])
                        return [e for e in events if e["pubkey"] == agent["pubkey"] and "原请求人当前" in e["content"]]
                    notices = wait_for("native receive-time source denial in " + slot, receiver_denial, timeout=30)
                    assert len(notices) == 1 and "owner" in notices[0]["content"]
                    assert not any(tag[0] == "p" for tag in notices[0]["tags"])
                    denied_ids.append(notices[0]["id"])
                rejected = current(require_ready=False)
                assert rejected["generation"] == observed["generation"] and rejected["active"] == {}
                assert all(checkpoint_is(slot, "A\n") for slot in slots), "receiver executed revoked work"
                report.setdefault("receive_authority_observations", []).append({
                    "scenario": mode, "generation": observed["generation"], "published_before_revocation": 4,
                    "admitted": 0, "checkpoint": "A", "native_denial_ids": denied_ids,
                    "same_generation_permission_restore": "not_yet_verified"})
                if expected_schema >= 7:
                    deliveries = set(rejected["deferred"])
                    assert len(deliveries) == 4
                    rechecked = wait_for("all native deferred deliveries rechecked while still denied", lambda: (
                        deferred_rechecks(current(require_ready=False), observed["generation"], deliveries,
                                          native_denial_counts(unit, observed["pid"]))), timeout=100)
                    for slot, notice_id in zip(slots, denied_ids):
                        events = transport.query([{"kinds": [9], "#h": [routes[slot]["tags"][0][1]],
                                                   "#e": [routes[slot]["id"]], "limit": 100}])
                        notices = [e for e in events if e["pubkey"] == agent["pubkey"] and "原请求人当前" in e["content"]]
                        assert [e["id"] for e in notices] == [notice_id], "native retries duplicated denial feedback"
                    assert all(checkpoint_is(slot, "A\n") for slot in slots)
                    report["receive_authority_observations"][-1]["durable_denial_rechecks"] = {
                        event_id: {"checks": rechecked["deferred"][event_id]["checks"],
                                   "notice_id": rechecked["deferred"][event_id]["notice"]["event"]["id"]}
                        for event_id in sorted(deliveries)}
                for channel in channels:
                    stack.buzz(owner, "channels", "add-member", "--channel", channel,
                               "--pubkey", source["pubkey"], "--role", "member", relay_http=relay["http_url"])

                # Drive automatic reconciliation repeatedly through the public
                # CLI, allowing the contract's 120s receipt timeout/backoff.
                # The delivered event/AttemptID stays fixed: neither TTL nor
                # source restoration authorizes a second visible continue.
                # Native must retain and recheck the blocked delivery itself.
                ticks = []
                report["receive_authority_observations"][-1]["controller_ticks"] = ticks
                def automatic_retry():
                    code, value = controller_sweep(mode + "-receiver-retry-" + str(len(ticks)))
                    ticks.append({"code": code, **value})
                    if mixed_sources:
                        for slot, delayed_slot in zip(slots, delayed):
                            execution_scope(slot, delayed_slot, source)
                    return all(checkpoint_is(slot, "A\nB\n") for slot in slots)
                wait_for("same-generation automatic retry after receiver denial", automatic_retry, timeout=180)
                assert current(require_ready=False)["generation"] == observed["generation"]
                report["receive_authority_observations"][-1].update(
                    same_generation_permission_restore="passed", controller_ticks=ticks)
                assert sum(tick["continued"] for tick in ticks) == 0, "same-generation retry must not create another delivered continue"
            elif receipt_timeout:
                hold_receiver_until_slow_notice(mode, slots)
            elif relay_outage:
                outage_then_recover(mode, slots)
            else:
                code, delivered = controller_sweep(mode)
                if code != int(mixed_sources):
                    raise RuntimeError(mode + " controller did not verify delivery")
                assert set(delivered["errors"]) == ({"source_not_member"} if mixed_sources else set())
                assert delivered["continued"] == 4
            if mixed_sources:
                observations = [wait_for("selected source execution scope", lambda s=slot, d=delayed_slot: execution_scope(s, d, source))
                                for slot, delayed_slot in zip(slots, delayed)]
                assert all(not (checkpoint_dir / (slot + ".checkpoint")).exists() for slot in delayed), "denied queued work reached execution"
                report.setdefault("mixed_execution_observations", []).append({
                    "scenario": mode, "selected_sources": observations, "denied_queued_sources": [original_inputs[slot] for slot in delayed]})
            wait_for(mode + " all four completed", lambda: all(checkpoint_is(slot, "A\nB\n") for slot in slots))
            wait_for(mode + " completion receipts", lambda: not current()["active"])
            for slot in slots:
                events = transport.query([{"kinds": [9], "#h": [routes[slot]["tags"][0][1]], "#e": [routes[slot]["id"]], "limit": 100}])
                continues = [e for e in events if e["pubkey"] == owner["pubkey"] and e["content"] == "@" + label + " continue"]
                assert len(continues) == 1, "missing or duplicated automatic continue"
                completed = [e for e in continues if current(require_ready=False)["receipts"].get(e["id"]) == "completed"]
                assert len(completed) == 1, "exactly one delivery must own completed original work"
                assert [t for t in completed[0]["tags"] if t[0] == "p"] == [["p", agent["pubkey"]]]
                if expected_schema >= 5:
                    assert [t[3:] for t in completed[0]["tags"] if t[0] == "recovery"] == [[original_inputs[slot]]]
                    binding = completed_source_binding(current, completed[0]["id"])
                    assert binding == [{"event_id": original_inputs[slot], "signed_author": source["pubkey"],
                                        "kind": "signed-event", "route": {"channel": routes[slot]["tags"][0][1], "root": routes[slot]["id"]}}]
                if expected_schema >= 8:
                    receipt = current(require_ready=False)
                    parts = [relay["http_url"].rstrip("/"), agent["pubkey"], receipt["generation"],
                             routes[slot]["tags"][0][1], routes[slot]["id"], [original_inputs[slot]]]
                    expected = hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()
                    assert receipt["recovery_attempts"][completed[0]["id"]] == expected
                    assert [t[1] for t in completed[0]["tags"] if t[0] == "recovery"] == [expected]
                    report["exact_attempt_receipts_verified"] = report.get("exact_attempt_receipts_verified", 0) + 1
                assert any(e["pubkey"] == agent["pubkey"] and e["content"] == "DONE " + slot for e in events), "no DONE in original Thread"
                if shutdown_outage:
                    ready_notices = [e for e in events if e["pubkey"] == owner["pubkey"] and "启动成功" in e["content"]]
                    assert len(ready_notices) == 1, "missing or duplicated coalesced lifecycle feedback"
                    reason = "停机提示未确认送达" if mode == "planned" else "异常中断"
                    assert reason in ready_notices[0]["content"] and "无需" in ready_notices[0]["content"]
                    assert not any("正在停止或重启" in e["content"] for e in events), "stale shutdown was replayed after ready"
                    assert not any(tag[0] == "p" for tag in ready_notices[0]["tags"])
                elif mode == "planned":
                    assert any("正在停止或重启" in e["content"] for e in events), "shutdown notice missing"
                private(work / (slot + "-events.json"), json.dumps(events, ensure_ascii=False, indent=2))
            if mixed_sources:
                # Restore only the queued originals. Their new subset attempt is
                # legitimate new work, never a retry/re-sign of the first bundle.
                for channel in channels:
                    stack.buzz(owner, "channels", "add-member", "--channel", channel,
                               "--pubkey", other["pubkey"], "--role", "member", relay_http=relay["http_url"])
                code, restored = controller_sweep(mode + "-restore-queued-source")
                assert code == 0 and restored["continued"] == 4 and restored["errors"] == []
                observed = [wait_for("queued source scoped execution", lambda d=delayed_slot, s=slot: execution_scope(d, s, other))
                            for slot, delayed_slot in zip(slots, delayed)]
                assert all(first["session_id"] != later["session_id"]
                           for first, later in zip(report["mixed_execution_observations"][-1]["selected_sources"], observed)), "new subset reused the previous source's provider context"
                wait_for("all restored queued work completes", lambda: all(checkpoint_is(slot, "A\nB\n") for slot in delayed))
                wait_for("restored queued receipts finish", lambda: not current()["active"])
                assert all(checkpoint_is(slot, "A\nB\n") for slot in slots), "restoration repeated original completed work"
                for slot, delayed_slot in zip(slots, delayed):
                    seed = routes[slot]
                    events = transport.query([{"kinds": [9], "#h": [seed["tags"][0][1]], "#e": [seed["id"]], "limit": 100}])
                    deliveries = [e for e in events if e["pubkey"] == owner["pubkey"] and e["content"] == "@" + label + " continue"]
                    assert len(deliveries) == 2, "each distinct source subset needs one automatic delivery"
                    delayed_delivery = [event for event in deliveries if [tag[3:] for tag in event["tags"] if tag[0] == "recovery"] == [[original_inputs[delayed_slot]]]]
                    assert len(delayed_delivery) == 1
                    assert completed_source_binding(current, delayed_delivery[0]["id"]) == [{
                        "event_id": original_inputs[delayed_slot], "signed_author": other["pubkey"], "kind": "signed-event",
                        "route": {"channel": seed["tags"][0][1], "root": seed["id"]}}]
                    assert any(e["pubkey"] == agent["pubkey"] and e["content"] == "DONE " + delayed_slot for e in events)
                report["mixed_execution_observations"][-1]["restored_queued_sources"] = observed
            checks.append({"scenario": mode, "status": "passed", "threads": 4, "channels": 2,
                           "old_generation": before["generation"], "new_generation": after["generation"]})
            print(json.dumps(checks[-1]), flush=True)
        if scenario != "default":
            result = recovery_scenarios.SCENARIOS[scenario](h)
            checks.append({"status": "passed", **result})
            print(json.dumps({"scenario": result["scenario"], "status": "passed"}), flush=True)
        assert checkpoint_is("completed-control", "A\nB\n")
        assert checkpoint_is("cancelled-control", "A\n")
        if waiting_controls:
            barrier()  # two FURTHER full ticks, not ticks that already ran
            settled = [controller_tick(f"controls-settle-{n}") for n in range(2)]
            summary = recovery_matrix.controls_untouched(
                controls=controls, before=control_baseline, after={slot: thread_events(slot) for slot in controls},
                prompts={slot: len(dispatches(slot)) for slot in controls},
                checkpoints={slot: checkpoint_text(slot) for slot in controls},
                owner=owner["pubkey"], label=label, ticks_after=len(settled))
            checks.append({"scenario": "REC-005 finished/cancelled/waiting-human/waiting-approval controls",
                           "status": "passed", **summary})
        else:
            checks.append({"scenario": "completed and cancelled controls", "status": "passed"})
        if timer is not None:
            evidence = timer.evidence()
            report["installed_timer"].update(evidence)
            seen, continued = set(), []
            for slot in routes:
                if routes[slot]["id"] in seen:
                    continue
                seen.add(routes[slot]["id"])
                continued += [e for e in thread_events(slot)
                              if e["pubkey"] == owner["pubkey"] and e["content"] == "@" + label + " continue"]
            summary = recovery_matrix.timer_only(
                invocations=timer.found, receipts=timer.receipts, missed=timer.missed, consumed=timer.consumed,
                direct_calls=len(direct_calls), manual_continue_sent=report["manual_continue_sent"], continues=continued,
                commands=timer.commands, service=timer.service)
            checks.append({"scenario": "installed timer is the only controller driver", "status": "passed", **summary})
        report["status"] = "passed"
    except BaseException as exc:
        report["status"] = "failed"
        report["failure"] = type(exc).__name__ + ": " + stack.redact(str(exc))[:300]
        raise
    finally:
        if timer is not None:
            # Stop the test timer first so no tick runs while the Agent stops.
            try:
                report["removed_test_units"] = timer.cleanup()
            except Exception as error:  # report loudly; never hide a leftover unit
                report["status"] = "failed"
                report["timer_cleanup_error"] = type(error).__name__ + ": " + str(error)[:200]
        if proxy is not None:
            proxy.close()
        report["direct_controller_calls"] = direct_calls
        # Only this run's exact transient service and label-owned containers.
        systemctl("stop", unit, check=False)
        logs = subprocess.run(["/usr/bin/journalctl", "--user", "-u", unit, "--no-pager", "-n", "1200"],
                              capture_output=True, text=True, timeout=15)
        private(work / "runtime.log", stack.redact(logs.stdout))
        stack.save_container_logs()
        report["removed_test_resources"] = stack.remove_relay_stack()
        report["finished_at"] = time.time()
        private(work / "report.json", json.dumps(report, ensure_ascii=False, indent=2))
        private(work / "report.html", "<!doctype html><meta charset=utf-8><title>Generic recovery L3</title><h1>Generic recovery L3</h1><pre>"
                + html.escape(json.dumps(report, ensure_ascii=False, indent=2)) + "</pre>")
        print(json.dumps({"status": report["status"], "report": str(work / "report.html")}), flush=True)
    return report


def run_matrix(binary, *, installed_timer, **options):
    """Every REC matrix scenario on its own fresh stack; one summary with per-scenario receipts."""
    rows = []
    for scenario, extra in MATRIX:
        started = time.time()
        row = dict(scenario=scenario, installed_timer=installed_timer, started_at=started)
        try:
            report = run(binary, installed_timer=installed_timer, scenario=scenario, **extra, **options)
            row["status"] = report["status"]
        except Exception as error:
            row.update(status="failed", failure=type(error).__name__ + ": " + stack.redact(str(error))[:300])
            report = None
        evidence = LAST_EVIDENCE / "report.json"
        row.update(report=str(evidence), report_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
                   seconds=round(time.time() - started, 1),
                   binary_sha256=(report or json.loads(evidence.read_text()))["binary_sha256"],
                   checks=[check.get("scenario") for check in (report or {}).get("checks", [])])
        rows.append(row)
        print(json.dumps(row), flush=True)
    summary = Path(tempfile.mkdtemp(prefix="buzz-recovery-l3-matrix-"))
    private(summary / "matrix.json", json.dumps(dict(level="L3", rows=rows), ensure_ascii=False, indent=2))
    print(json.dumps({"matrix": str(summary / "matrix.json"),
                      "status": "passed" if all(row["status"] == "passed" for row in rows) else "failed"}), flush=True)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--lazy-pool", action="store_true", help="Require journal-driven provider prewarm after restart")
    parser.add_argument("--idle-pool-sleep", type=int, default=0, help="Verify readiness after actual provider idle shutdown")
    parser.add_argument("--source-membership", action="store_true", help="Require current original-requester authority, never owner-continue substitution")
    parser.add_argument("--forged-sender", action="store_true", help="In anyone mode, inject a member-signed recovery and require native rejection with an actionable notice")
    parser.add_argument("--receive-membership", action="store_true", help="Revoke after automatic publication but before receipt, then require same-generation automatic retry")
    parser.add_argument("--mixed-sources", action="store_true", help="Queue a second requester in each live Thread, revoke it, verify ACP subset isolation then restore its original work")
    parser.add_argument("--receipt-timeout", action="store_true", help="Hold exact receiver beyond 120 seconds, require one slow notice per Thread, then automatic recovery without another continue")
    parser.add_argument("--relay-outage", action="store_true", help="Stop only the isolated Relay, observe durable nonzero retry wait, then restore it and require automatic original-work recovery")
    parser.add_argument("--shutdown-outage", action="store_true", help="Stop isolated Relay before planned shutdown; require durable notice intent and coalesced feedback after restart")
    parser.add_argument("--systemd-ticks", action="store_true", help="Run controller via owned real oneshots and verify fresh durable receipts; not an installed timer")
    parser.add_argument("--installed-timer", action="store_true",
                        help="Install the installer-rendered service/timer under a unique test name; recover only from timer ticks")
    parser.add_argument("--waiting-controls", action="store_true",
                        help="REC-005: add waiting-human and waiting-approval controls; require zero continue/execution over two ticks")
    scenario = parser.add_mutually_exclusive_group()
    scenario.add_argument("--lost-ack", dest="scenario", action="store_const", const="lost-ack",
                          help="REC-006: publish succeeds but the ACK is dropped by a local proxy; require exact readback")
    scenario.add_argument("--crash-before-admission", dest="scenario", action="store_const", const="crash-before-admission",
                          help="REC-007: deliver to a frozen receiver, crash again; one new-generation attempt, old one fenced")
    scenario.add_argument("--crash-after-admission", dest="scenario", action="store_const", const="crash-after-admission",
                          help="REC-008: crash while admitted resumed work runs; new journal takes over without parallel dispatch")
    scenario.add_argument("--cancel-interleave", dest="scenario", action="store_const", const="cancel-interleave",
                          help="REC-015: queued work, slow cancel and post-cancel work; cancelled never revived")
    scenario.add_argument("--stuck-target", dest="scenario", action="store_const", const="stuck-target",
                          help="REC-019: one blocked and one never-finishing target beside five recoverable Threads")
    scenario.add_argument("--matrix", dest="scenario", action="store_const", const="matrix",
                          help="Run the default scenario and every REC scenario above, each on a fresh stack, with waiting controls")
    parser.add_argument("--expected-schema", type=int, choices=(2, 3, 4, 5, 6, 7, 8, 9, 10), default=10,
                        help="Pinned protocol under test; older versions only reproduce historical behavior red")
    args = parser.parse_args()
    if args.scenario == "matrix":
        rows = run_matrix(args.binary.resolve(strict=True), installed_timer=args.installed_timer,
                          expected_schema=args.expected_schema)
        raise SystemExit(int(any(row["status"] != "passed" for row in rows)))
    run(args.binary.resolve(strict=True), lazy_pool=args.lazy_pool, idle_pool_sleep=args.idle_pool_sleep,
        expected_schema=args.expected_schema, source_membership=args.source_membership, forged_sender=args.forged_sender,
        receive_membership=args.receive_membership, mixed_sources=args.mixed_sources, receipt_timeout=args.receipt_timeout,
        relay_outage=args.relay_outage, shutdown_outage=args.shutdown_outage, systemd_ticks=args.systemd_ticks,
        installed_timer=args.installed_timer, waiting_controls=args.waiting_controls,
        scenario=args.scenario or "default")
