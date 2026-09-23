"""Shared helpers for the L2-3 (real Desk) and L3 (routing) tests on the localstack.

Both layers need the localstack up with its Desk and role buzz-acp agents running on the current prompts:
  python3 skills/buzz-agent-setup/tests/localstack/stack.py agents --restart desk,role
They share the GitLab project and channel with L2-2, so never run them concurrently with L2-2 or each other.
Only 127.0.0.1 services are contacted. Sync runs from `stack.py timer-run` (ADR-0008), and routing is
the deterministic Canvas gate under the Desk identity, never a route Workflow.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

try:
    from .test_sync_local import STATE_FILE, SKILL, Stack, first_line, iso, p_tags, tag_values, utc_now, wait_for
except ImportError:  # imported as a top-level module
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_sync_local import STATE_FILE, SKILL, Stack, first_line, iso, p_tags, tag_values, utc_now, wait_for

ENABLED = os.environ.get("BUZZ_SYNC_L3") == "1"
STACK_PY = SKILL / "tests" / "localstack" / "stack.py"
WORKFLOWS = SKILL / "references" / "workflows"
ROUTE_SCRIPT = SKILL / "scripts" / "gitlab_buzz_route_reply.py"
DESK_NAME, ROLE_NAME = "buzz-sync-desk", "buzz-sync-role"
AGENT_TIMEOUT = 480  # a cold LLM turn of the role agent (or the ordinary Desk) plus relay propagation
ROUTE_TIMEOUT = 90  # deterministic Desk route gate plus relay propagation
QUIET = 60  # how long "nothing else happens" is watched
TEST_WORKFLOW_PREFIXES = ("gitlab-buzz-sync-tick-", "gitlab-route-")


def load_sync_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_e2e", SKILL / "scripts" / "gitlab_buzz_sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from texts(child)
    elif isinstance(value, list):
        for child in value:
            yield from texts(child)


def is_route_event(event: dict) -> bool:
    """A route message keeps its human mention first and its machine marker on a separate line."""

    return any(line.startswith("[gitlab-route:v2]")
               for line in str(event.get("content") or "").splitlines())


def codex_tool_outputs(sessions_dir: Path, workdir: str, started_iso: str) -> list[str]:
    """Completed command stdout from Codex rollouts rooted in exactly `workdir` after `started_iso`."""

    found: list[tuple[str, str]] = []
    for session in sorted(sessions_dir.rglob("*.jsonl")):
        in_scope = False
        for line in session.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("type") == "session_meta":
                in_scope = (record.get("payload") or {}).get("cwd") == workdir
            elif record.get("type") == "turn_context":
                in_scope = (record.get("payload") or {}).get("cwd") == workdir
            if not in_scope or str(record.get("timestamp") or "") < started_iso:
                continue
            item = (record.get("payload") or {}).get("item") or {}
            stdout = item.get("stdout")
            if item.get("type") == "CommandExecution" and isinstance(stdout, str) and stdout:
                found.append((str(record.get("timestamp") or ""), stdout))
    return [stdout for _, stdout in sorted(found)]


def stack_cli(*args: str) -> dict:
    env = {"HOME": str(Path.home()), "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
    proc = subprocess.run([sys.executable, str(STACK_PY), *args], capture_output=True, text=True, timeout=600, env=env)
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        result = {}
    if proc.returncode != 0 or not result.get("ok"):
        raise AssertionError(f"stack.py {' '.join(args)} failed: exit {proc.returncode} {json.dumps(result)[:400]}")
    return result


def dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from dicts(child)


def _filled(text: str) -> str:
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    leftover = re.findall(r"<[a-z][a-z-]*>", body)
    if leftover:
        raise AssertionError(f"unfilled template placeholders: {leftover}")
    return text


def route_yaml(template: str, name: str, publisher_pubkey: str) -> str:
    """Legacy HTTP/native routing helper retained only for explicit fallback tests."""

    text = (WORKFLOWS / template).read_text(encoding="utf-8")
    text = text.replace("<publisher-hex-pubkey>", publisher_pubkey).replace("<role-name>", ROLE_NAME)
    text = re.sub(r"(?m)^name: .*$", f"name: {name}", text, count=1)
    return _filled(text)


class E2ECase(unittest.TestCase):
    maxDiff = None
    stack: Stack
    relay_pubkey: str
    required_agents = ("desk", "role")

    @classmethod
    def setUpClass(cls):
        cls.stack = Stack()
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        cls.relay_pubkey = state["identities"]["relay"]["pubkey"]
        desk = ((state.get("agents") or {}).get("desk") or {})
        cls.desk_adapter = desk.get("adapter", "claude")
        for kind in cls.required_agents:
            if ((state.get("agents") or {}).get(kind) or {}).get("status") != "running":
                raise AssertionError(f"{kind} agent is not running; start the localstack with --with-{kind}")
        cls.desk_workdir = desk.get("workdir", "")
        cls.sync_config_file = Path(state["sync_config_file"])
        cls.sync_state_dir = Path(state["sync_state_dir"])
        cls.purge_test_workflows()

    @classmethod
    def purge_test_workflows(cls):
        """Leftover (retired) schedule or route workflows from an interrupted run would make later cases pass or fail falsely."""

        listed = cls.stack.buzz("owner", "workflows", "list", "--channel", cls.stack.channel)
        for item in listed if isinstance(listed, list) else []:
            name = next((line[len("name: "):] for line in str(item.get("content") or "").splitlines()
                         if line.startswith("name: ")), "")
            if name.startswith(TEST_WORKFLOW_PREFIXES) and isinstance(item.get("workflow_id"), str):
                try:
                    cls.stack.buzz("owner", "workflows", "delete", "--workflow", item["workflow_id"])
                except ValueError:
                    pass

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.stack.homes, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bsls-e2e-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.stamp = f"{self._testMethodName.split('_')[1]}-{secrets.token_hex(3)}"
        self.started_unix = int(time.time()) - 5
        self.since = iso(utc_now() - dt.timedelta(minutes=1))
        stack_cli("sync-config", "--since", self.since)
        self.set_route_canvas()

    def route_canvas(self) -> str:
        issue = "[gitlab-notify:v1][object:issue][type:feature][status:ready][state:opened][change:routing]"
        mr = "[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle][transition:reviewable]"
        return f"""# Localstack Channel context

<!-- gitlab-buzz-routing:v1 -->
| route_id | trigger_prefix | role | reason |
| --- | --- | --- | --- |
| feature-ready | `{issue}` | `role` | `feature+ready` |
| mr-reviewable | `{mr}` | `role` | `mr+reviewable` |
<!-- /gitlab-buzz-routing -->
"""

    def set_route_canvas(self, content: str | None = None, *, who: str = "owner") -> str:
        sent = self.stack.buzz(
            who, "canvas", "set", "--channel", self.stack.channel,
            "--content", self.route_canvas() if content is None else content,
        )
        return self.sent_id(sent)

    def route_config(self) -> dict:
        return {
            "scan_since": self.since,
            "sender_pubkey": self.stack.desk["pubkey"],
            "channels": {
                self.stack.channel: {
                    "publisher_pubkey": self.stack.desk["pubkey"],
                    "canvas_admin_pubkeys": [self.stack.owner["pubkey"]],
                    "roles": {
                        "role": {
                            "mention": f"@{ROLE_NAME}",
                            "mention_pubkey": self.stack.role_pubkey,
                        }
                    },
                }
            },
            "buzz": {"cli_path": str(self.stack.cli), "cli_sha256": self.stack.cli_sha256},
        }

    def run_route(self, *, expect_ok: bool = True) -> dict:
        config_path = self.tmp / "route-config.json"
        config_path.write_text(json.dumps(self.route_config()), encoding="utf-8")
        config_path.chmod(0o600)
        state_dir = self.tmp / "route-state"
        state_dir.mkdir(mode=0o700, exist_ok=True)
        home = self.tmp / "route-home"
        home.mkdir(mode=0o700, exist_ok=True)
        env = {
            "HOME": str(home), "PATH": "/usr/bin:/bin",
            "BUZZ_RELAY_URL": self.stack.relay_http,
            "BUZZ_PRIVATE_KEY": self.stack.desk["secret"],
            "BUZZ_AUTH_TAG": self.stack.desk["auth_tag"],
        }
        proc = subprocess.run(
            ["python3", str(ROUTE_SCRIPT), "--config", str(config_path),
             "--state-dir", str(state_dir), "--scan-once"],
            env=env, cwd=SKILL.parents[1], capture_output=True, text=True, timeout=120,
        )
        try:
            result = json.loads(proc.stdout or proc.stderr)
        except ValueError:
            result = {"status": "unparseable", "stdout": proc.stdout[-200:], "stderr": proc.stderr[-200:]}
        if expect_ok:
            self.assertEqual((proc.returncode, result.get("status")), (0, "ok"), result)
        else:
            self.assertNotEqual(proc.returncode, 0, result)
        return result

    # ── workflows ───────────────────────────────────────────────────────────

    def create_workflow(self, yaml: str) -> str:
        created = self.stack.buzz("owner", "workflows", "create", "--channel", self.stack.channel, "--yaml", yaml)
        workflow_id = next((d["workflow_id"] for d in dicts(created) if isinstance(d.get("workflow_id"), str)), None)
        self.assertTrue(workflow_id, created)
        self.addCleanup(self.delete_workflow, workflow_id)
        return workflow_id

    def delete_workflow(self, workflow_id: str) -> None:
        try:
            self.stack.buzz("owner", "workflows", "delete", "--workflow", workflow_id)
        except ValueError:  # the delete printed no JSON; the command itself succeeded
            pass

    # ── Buzz reads ──────────────────────────────────────────────────────────

    def channel_events(self) -> list[dict]:
        found: dict[str, dict] = {}
        before = None
        while True:
            args = ["messages", "get", "--channel", self.stack.channel, "--since", str(self.started_unix), "--limit", "200"]
            if before is not None:
                args += ["--before", str(before)]
            page = self.stack.buzz("owner", *args)
            fresh = [event for event in page if event["id"] not in found]
            found.update((event["id"], event) for event in fresh)
            if len(page) < 200 or not fresh:
                break
            before = min(event["created_at"] for event in page)
        return sorted(found.values(), key=lambda event: (event["created_at"], event["id"]))

    @staticmethod
    def by(events: list[dict], pubkey: str) -> list[dict]:
        return [event for event in events if event.get("pubkey") == pubkey]

    @staticmethod
    def replies_to(events: list[dict], event_id: str) -> list[dict]:
        return [event for event in events if any(len(tag) > 1 and tag[1] == event_id for tag in tag_values(event, "e"))]

    def sent_id(self, sent) -> str:
        event_id = next((d["event_id"] for d in dicts(sent) if isinstance(d.get("event_id"), str)), None)
        self.assertTrue(event_id, sent)
        return event_id

    def only_root(self, kind: str, iid: int) -> dict:
        roots = self.stack.roots(kind, iid, self.started_unix)
        self.assertEqual(len(roots), 1, f"{kind} {iid} roots: {[first_line(root) for root in roots]}")
        return roots[0]

    def wait_root(self, kind: str, iid: int, timeout: float = AGENT_TIMEOUT) -> dict:
        wait_for(f"{kind} {iid} root", lambda: self.stack.roots(kind, iid, self.started_unix), timeout, 5)
        return self.only_root(kind, iid)

    def assert_single_binding(self, kind: str, iid: int, root: dict) -> None:
        self.assertEqual([binding["root_event_id"] for binding in self.stack.bindings(kind, iid)], [root["id"]])

    def route_replies(self, root_id: str) -> list[dict]:
        return [event for event in self.stack.thread(root_id)
                if event.get("pubkey") == self.stack.desk["pubkey"]
                and self.stack.role_pubkey in p_tags(event)
                and is_route_event(event)]

    def role_replies(self, root_id: str) -> list[dict]:
        return [event for event in self.stack.thread(root_id) if event.get("pubkey") == self.stack.role_pubkey]

    def wait_route(self, root_id: str) -> list[dict]:
        return wait_for("Desk Canvas route reply", lambda: self.route_replies(root_id), ROUTE_TIMEOUT, 2)

    def wait_role(self, root_id: str) -> list[dict]:
        return wait_for("role agent reply in the same thread", lambda: self.role_replies(root_id), AGENT_TIMEOUT, 5)

    # ── observing the real Desk ─────────────────────────────────────────────

    def desk_tool_output_since(self, started_iso: str) -> list[str]:
        """Tool results recorded by the configured Desk adapter after `started_iso`."""

        if self.desk_adapter == "codex":
            return codex_tool_outputs(Path.home() / ".codex" / "sessions", self.desk_workdir, started_iso)

        project_dir = Path.home() / ".claude" / "projects" / re.sub(r"[/.]", "-", self.desk_workdir)
        found = []
        for session in project_dir.glob("*.jsonl"):
            for line in session.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if str(record.get("timestamp") or "") < started_iso:
                    continue
                for block in (record.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        found.extend(texts(block.get("content")))
        return found

    def mention_desk(self) -> str:
        return self.sent_id(self.stack.buzz("owner", "messages", "send", "--channel", self.stack.channel,
                                            "--content", f"@{DESK_NAME} gitlab sync"))

    def hold_sync_lock(self) -> None:
        sync = load_sync_module()
        self.sync_state_dir.mkdir(mode=0o700, exist_ok=True)
        config = json.loads(self.sync_config_file.read_text(encoding="utf-8"))
        fd = os.open(sync.lock_path(config, self.sync_state_dir), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)

    # ── deterministic Desk sync + route scripts ─────────────────────────────

    def run_sync(self):
        config = self.stack.config(self.since, people={})
        run = self.stack.run_sync(config, self.tmp)
        self.assertEqual((run.returncode, run.status), (0, "ok"), run.describe())
        if run.result.get("summary_requests"):
            # durable summary requests back-pressure the NEXT scan at its very
            # start; ack them through the real claim + publisher exactly like a
            # Desk turn, then redo the run so callers see unblocked counters.
            for request in run.result.get("summary_requests") or []:
                self.stack.claim_oldest_summary(config, self.tmp)
                facts = request.get("facts") or []
                prose = f"本轮共有 {len(facts)} 条分支活动。"
                outcome = self.stack.run_publisher(
                    config, self.tmp, prose, request.get("facts_sha256", ""))
                assert outcome.get("status") in {"sent", "duplicate"}, outcome
            run = self.stack.run_sync(config, self.tmp)
            self.assertEqual((run.returncode, run.status), (0, "ok"), run.describe())
        self.run_route()
        return run
