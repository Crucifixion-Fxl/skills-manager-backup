"""L2-2 integration: gitlab_buzz_sync.py × local Buzz relay × local marginalia GitLab, no LLM.

Skipped unless BUZZ_SYNC_L2=1. Needs the localstack up and the GitLab fixture applied:
  python3 skills/buzz-agent-setup/tests/localstack/stack.py up --with-desk --with-role   # or already up
  python3 skills/buzz-agent-setup/tests/localstack/stack.py fixture
  BUZZ_SYNC_L2=1 python3 -m unittest skills/buzz-agent-setup/tests/integration/test_sync_local.py -v

Each case creates uniquely titled objects in the localstack GitLab project, runs the real script in a
subprocess with an explicitly built environment, and asserts through the raw Buzz 0.5.23 CLI (as the
Desk) and the GitLab API. Only 127.0.0.1 services are contacted. Nothing here mentions the Desk or role
agents: `people` maps GitLab users to the owner key and to a fresh non-agent member key.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENABLED = os.environ.get("BUZZ_SYNC_L2") == "1"
SKILL = Path(__file__).resolve().parents[2]
REPO = SKILL.parents[1]
SCRIPT_REL = "skills/buzz-agent-setup/scripts/gitlab_buzz_sync.py"
STATE_FILE = SKILL / "tests" / "localstack" / ".state" / "state.json"
REF_SCRIPTS = SKILL / "references" / "scripts"
OWNER_WRAPPER = Path.home() / ".local" / "bin" / "buzz"
GITLAB_BASE = "http://127.0.0.1:8929"
GITLAB_CONTAINER = "docker-gitlab-1"
TOKEN_ENV = "LOCALSTACK_GITLAB_TOKEN"
BINDING_RE = re.compile(r"<!-- gitlab-buzz-binding:v1 (\{.*\}) -->")
CI_YAML = """test:unit:
  stage: test
  script: ["exit 1"]
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
      when: manual
"""
RUBY_DROP = r"""
require 'json'
project = Project.find(ENV.fetch('BSLS_PROJECT_ID').to_i)
pipeline = project.all_pipelines.find(ENV.fetch('BSLS_PIPELINE_ID').to_i)
dropped = []
pipeline.builds.each do |build|
  next if build.complete?
  build.drop!(:script_failure)
  dropped << build.name
end
puts 'BSLS_JSON=' + JSON.generate({ 'dropped' => dropped, 'status' => pipeline.reload.status })
"""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def require_loopback(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    if not ipaddress.ip_address(host).is_loopback:
        raise AssertionError(f"refusing non-loopback address {url!r}")
    return url


def wait_for(what: str, predicate, timeout: float = 90.0, interval: float = 1.0):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {int(timeout)}s waiting for {what}")
        time.sleep(interval)


def first_line(event: dict) -> str:
    """Machine header line: last line on new facts, first line on legacy facts."""

    content = str(event.get("content") or "")
    lines = [line for line in content.split("\n") if line]
    if not lines:
        return ""
    if lines[0].startswith("[gitlab-notify:v1]"):
        return lines[0]
    if lines[-1].startswith("[gitlab-notify:v1]"):
        return lines[-1]
    return lines[0]


PLAQUE_TAILS = (
    ("issue", re.compile(r"/-/(?:issues|work_items)/([1-9][0-9]*)$")),
    ("mr", re.compile(r"/-/merge_requests/([1-9][0-9]*)$")),
)


def plaque_object(event: dict) -> tuple[str, int] | None:
    """(kind, iid) of a header-less plaque root: its last non-empty line is one bare object URL."""

    content = [line for line in str(event.get("content") or "").split("\n") if line]
    if len(content) < 3 or not re.fullmatch(r"https?://[^\s]+", content[-1]):
        return None
    for kind, tail in PLAQUE_TAILS:
        match = tail.search(content[-1])
        if match:
            return kind, int(match.group(1))
    return None


HEADER_TRAILER_RE = re.compile(r"(?:\[desc:[^\]]*\])?(?:\[note:[^\]]*\])?(?:\[events:[^\]]*\])?$")


def header(event: dict) -> str:
    """The machine header without its script-only trailer ([desc:…][note:…][events:…])."""

    return HEADER_TRAILER_RE.sub("", first_line(event))


def lines(event: dict) -> list[str]:
    return str(event.get("content") or "").split("\n")


def tag_values(event: dict, name: str) -> list[list]:
    return [tag for tag in event.get("tags") or [] if isinstance(tag, list) and tag and tag[0] == name]


def p_tags(event: dict) -> set[str]:
    return {tag[1] for tag in tag_values(event, "p") if len(tag) > 1}


def event_keys(event: dict) -> set[str]:
    keys: set[str] = set()
    trailer = re.search(r"\[events:([^\]]*)\]$", first_line(event))
    if trailer:
        keys |= set(trailer.group(1).split(","))
    for line in lines(event)[1:]:
        if line.startswith("events: "):
            keys |= set(line[len("events: "):].split(","))
    return keys


class SyncRun:
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
        try:
            self.result = json.loads(stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            self.result = {}

    @property
    def status(self):
        return self.result.get("status")

    def describe(self) -> str:
        return f"exit={self.returncode} result={json.dumps(self.result)[:600]} stderr={self.stderr.strip()[-400:]}"


class Stack:
    """The running localstack: relay, channel, identities, GitLab project, users and PATs."""

    def __init__(self):
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AssertionError(f"localstack state unreadable ({STATE_FILE}); run stack.py up") from exc
        if state.get("status") != "up":
            raise AssertionError("localstack is not up; run stack.py up")
        relay, gitlab = state["relay"], state["gitlab"]
        self.state = state
        self.relay_http = require_loopback(relay["http_url"])
        self.channel = relay["channel_id"]
        cli = Path(state["buzz_cli"])
        if (not cli.is_absolute() or cli.name != "buzz" or "buzz-0.5.23" not in cli.parts or cli.is_symlink()
                or cli == OWNER_WRAPPER or (OWNER_WRAPPER.exists() and cli.resolve() == OWNER_WRAPPER.resolve())):
            raise AssertionError(f"refusing Buzz CLI {cli}: must be the raw buzz-0.5.23 binary")
        self.cli = cli
        self.cli_sha256 = hashlib.sha256(cli.read_bytes()).hexdigest()
        ids = state["identities"]
        self.desk = self._identity(ids["desk"])
        self.owner = self._identity(ids["owner"])
        self.role = self._identity(ids["role"])
        self.role_pubkey = self.role["pubkey"]
        self.route = self._identity(ids["route"])
        if require_loopback(gitlab["base_url"]) != GITLAB_BASE:
            raise AssertionError(f"unexpected GitLab base {gitlab['base_url']}")
        self.project_id = gitlab["project_id"]
        self.users, self.tokens = {}, {}
        for role in ("bot", "outsider", "dev", "maintainer"):
            rec = gitlab.get(role) or {}
            if not rec.get("pat_file"):
                raise AssertionError(f"GitLab {role} PAT missing; run stack.py fixture")
            self.users[role] = {"id": rec["user_id"], "username": rec["username"]}
            self.tokens[role] = Path(rec["pat_file"]).read_text(encoding="utf-8").strip()
        self.homes = Path(tempfile.mkdtemp(prefix="bsls-l2-homes-"))
        self.member_pubkeys = sorted(relay.get("member_pubkeys") or [])
        if not self.member_pubkeys:
            raise AssertionError("localstack channel member snapshot missing; recreate the stack")
        self._secrets = [self.desk["secret"], self.owner["secret"], self.role["secret"],
                         self.route["secret"],
                         *self.tokens.values()]

    @staticmethod
    def _identity(rec: dict) -> dict:
        ident = {"pubkey": rec["pubkey"], "secret": Path(rec["secret_file"]).read_text(encoding="utf-8").strip()}
        if rec.get("auth_tag_file"):
            ident["auth_tag"] = Path(rec["auth_tag_file"]).read_text(encoding="utf-8").strip()
        return ident

    def redact(self, text: str) -> str:
        for value in self._secrets:
            text = text.replace(value, "<redacted>")
        return re.sub(r"glpat-[A-Za-z0-9._-]+", "glpat-<redacted>", text)

    # ── GitLab ──────────────────────────────────────────────────────────────

    def api(self, role: str, method: str, path: str, params=None, body=None, token: str | None = None):
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = require_loopback(f"{GITLAB_BASE}/api/v4/{path.lstrip('/')}" + (f"?{query}" if query else ""))
        request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"PRIVATE-TOKEN": token or self.tokens[role],
                                                  "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=60) as response:
                status, headers, raw = response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            status, headers, raw = exc.code, exc.headers, exc.read()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return status, headers, parsed

    def ok(self, role: str, method: str, path: str, params=None, body=None):
        status, _, parsed = self.api(role, method, path, params, body)
        if status not in (200, 201, 202, 204):
            raise AssertionError(f"GitLab {method} {path} as {role}: HTTP {status} {self.redact(json.dumps(parsed))[:300]}")
        return parsed

    def paged(self, role: str, path: str, params=None) -> list:
        page, items = 1, []
        while True:
            status, headers, parsed = self.api(role, "GET", path, {**(params or {}), "page": page, "per_page": 100})
            if status != 200 or not isinstance(parsed, list):
                raise AssertionError(f"GitLab GET {path}: HTTP {status}")
            items += parsed
            nxt = headers.get("X-Next-Page") or ""
            if not nxt:
                return items
            page = int(nxt)

    def server_time(self) -> dt.datetime:
        _, headers, _ = self.api("bot", "GET", "user")
        return email.utils.parsedate_to_datetime(headers["Date"])

    @property
    def base(self) -> str:
        return f"projects/{self.project_id}"

    def create_issue(self, title: str, labels: str = "type::feature,status::backlog", **extra) -> dict:
        return self.ok("dev", "POST", f"{self.base}/issues", body={"title": title, "labels": labels,
                                                                    "description": "l2 integration", **extra})

    def notes(self, kind: str, iid: int) -> list[dict]:
        noun = "issues" if kind == "issue" else "merge_requests"
        return self.paged("bot", f"{self.base}/{noun}/{iid}/notes", {"sort": "asc"})

    def bindings(self, kind: str, iid: int) -> list[dict]:
        found = []
        for note in self.notes(kind, iid):
            match = BINDING_RE.fullmatch(str(note.get("body") or "").split("\n", 1)[0])
            if match and (note.get("author") or {}).get("id") == self.users["bot"]["id"]:
                found.append({"note_id": note["id"], "body": note["body"], **json.loads(match.group(1))})
        return found

    def branch_with_files(self, branch: str, files: dict[str, str], message: str) -> str:
        self.ok("dev", "POST", f"{self.base}/repository/branches", body={"branch": branch, "ref": "main"})
        return self.commit(branch, files, message, action="create")

    def commit(self, branch: str, files: dict[str, str], message: str, action: str = "update") -> str:
        commit = self.ok("dev", "POST", f"{self.base}/repository/commits", body={
            "branch": branch, "commit_message": message,
            "actions": [{"action": action, "file_path": path, "content": content} for path, content in files.items()]})
        return commit["id"]

    def create_mr(self, branch: str, title: str, reviewers: tuple[str, ...] = (), description: str = "") -> dict:
        return self.ok("dev", "POST", f"{self.base}/merge_requests", body={
            "source_branch": branch, "target_branch": "main", "title": title, "description": description,
            "reviewer_ids": [self.users[role]["id"] for role in reviewers]})

    def mr(self, iid: int) -> dict:
        return self.ok("bot", "GET", f"{self.base}/merge_requests/{iid}")

    def rails_drop_pipeline(self, pipeline_id: int) -> dict:
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(Path.home())}
        remote = f"/tmp/bsls-l2-drop-{secrets.token_hex(4)}.rb"
        subprocess.run(["docker", "exec", "-i", GITLAB_CONTAINER, "sh", "-c", f"umask 022; cat > {remote}"],
                       input=RUBY_DROP, text=True, check=True, capture_output=True, env=env, timeout=60)
        try:
            proc = subprocess.run(["docker", "exec", "-e", f"BSLS_PROJECT_ID={self.project_id}",
                                   "-e", f"BSLS_PIPELINE_ID={pipeline_id}", GITLAB_CONTAINER,
                                   "gitlab-rails", "runner", remote], text=True, capture_output=True, env=env,
                                  timeout=600)
        finally:
            subprocess.run(["docker", "exec", GITLAB_CONTAINER, "rm", "-f", remote], capture_output=True, env=env,
                           timeout=60)
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("BSLS_JSON=")), None)
        if proc.returncode != 0 or line is None:
            raise AssertionError(f"rails drop failed: {self.redact(proc.stderr)[-500:]}")
        return json.loads(line.split("=", 1)[1])

    # ── Buzz (raw CLI as a localstack identity) ─────────────────────────────

    def buzz(self, who: str, *args: str):
        ident = {"desk": self.desk, "owner": self.owner,
                 "role": self.role, "route": self.route}[who]
        home = self.homes / who
        home.mkdir(mode=0o700, exist_ok=True)
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "BUZZ_RELAY_URL": self.relay_http,
               "BUZZ_PRIVATE_KEY": ident["secret"]}
        if ident.get("auth_tag"):
            env["BUZZ_AUTH_TAG"] = ident["auth_tag"]
        proc = subprocess.run([str(self.cli), *args], env=env, capture_output=True, text=True, timeout=90)
        if proc.returncode != 0:
            raise AssertionError(f"buzz {' '.join(args[:2])} as {who} exit {proc.returncode}: "
                                 f"{self.redact(proc.stderr or proc.stdout)[-400:]}")
        return json.loads(proc.stdout)

    def thread(self, root: str) -> list[dict]:
        events = self.buzz("desk", "messages", "thread", "--channel", self.channel, "--event", root, "--limit", "500")
        return sorted(events, key=lambda e: (e["created_at"], e["id"]))

    def publisher_thread(self, root: str, kind: int = 9) -> list[dict]:
        return [e for e in self.thread(root) if e.get("pubkey") == self.desk["pubkey"] and e.get("kind") == kind]

    def publisher_channel_messages(self, since_unix: int) -> list[dict]:
        found: dict[str, dict] = {}
        before = None
        while True:  # the CLI clamps --limit to 200, so page backwards by time
            args = ["messages", "get", "--channel", self.channel, "--since", str(since_unix), "--limit", "200"]
            if before is not None:
                args += ["--before", str(before)]
            page = self.buzz("desk", *args)
            fresh = [e for e in page if e["id"] not in found]
            found.update((e["id"], e) for e in fresh)
            if len(page) < 200 or not fresh:
                break
            before = min(e["created_at"] for e in page)
        return sorted((e for e in found.values() if e.get("pubkey") == self.desk["pubkey"]),
                      key=lambda e: (e["created_at"], e["id"]))

    def roots(self, kind: str, iid: int, since_unix: int) -> list[dict]:
        """Desk top-level roots of one object: a fact header or a legacy header-less plaque."""

        suffix = f"[project:{self.project_id}][{kind}:{iid}]"
        found = []
        for event in self.publisher_channel_messages(since_unix):
            if tag_values(event, "e"):
                continue
            head = first_line(event)
            if head.startswith(f"[gitlab-notify:v1][object:{kind}]") and head.endswith(suffix):
                found.append(event)
            elif plaque_object(event) == (kind, iid):
                found.append(event)
        return found

    def ensure_member(self, pubkey: str) -> None:
        self.buzz("owner", "channels", "add-member", "--channel", self.channel, "--pubkey", pubkey, "--role", "member")

    # ── the script under test ───────────────────────────────────────────────

    def config(self, since: str, **overrides) -> dict:
        cfg = {
            "channel_id": self.channel, "publisher_pubkey": self.desk["pubkey"], "since": since,
            "gitlab": {"base_url": GITLAB_BASE, "token_env": TOKEN_ENV, "bot_user_id": self.users["bot"]["id"],
                       "bot_username": self.users["bot"]["username"], "projects": [self.project_id]},
            "buzz": {"cli_path": str(self.cli), "cli_sha256": self.cli_sha256},
            "people": {self.users["maintainer"]["username"]: self.owner["pubkey"]},
            "diff": {"enabled": False, "private": False},
        }
        for key, value in overrides.items():
            if key == "gitlab":
                cfg["gitlab"] = {**cfg["gitlab"], **value}
            else:
                cfg[key] = value
        return cfg

    def sync_command(self, config: dict, workdir: Path, token: str | None = None):
        workdir.mkdir(parents=True, exist_ok=True)
        config_path = workdir / f"config-{secrets.token_hex(4)}.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        config_path.chmod(0o600)
        home = workdir / "home"
        home.mkdir(mode=0o700, exist_ok=True)
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "BUZZ_RELAY_URL": self.relay_http,
               "BUZZ_PRIVATE_KEY": self.desk["secret"], "BUZZ_AUTH_TAG": self.desk["auth_tag"],
               TOKEN_ENV: self.tokens["bot"] if token is None else token}
        args = ["python3", SCRIPT_REL, "--config", str(config_path), "--state-dir", str(workdir / "state")]
        return args, env

    def run_sync(self, config: dict, workdir: Path, token: str | None = None) -> SyncRun:
        args, env = self.sync_command(config, workdir, token)
        proc = subprocess.run(args, env=env, cwd=REPO, capture_output=True, text=True, timeout=900)
        return SyncRun(proc.returncode, self.redact(proc.stdout), self.redact(proc.stderr))

    def claim_oldest_summary(self, config: dict, workdir: Path) -> None:
        """The fixed runner's durable claim (PENDING -> SUMMARIZING), as the Desk turn would."""

        release = Path(self.state["runner_release_dir"])
        claim_config = workdir / "claim-config.json"
        claim_config.write_text(json.dumps(config), encoding="utf-8")
        claim_config.chmod(0o600)
        (workdir / "state").mkdir(parents=True, exist_ok=True)
        script = workdir / "claim_summary.py"
        script.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, %r)\n"
            "import gitlab_buzz_sync as s\n"
            "scopes = [(json.loads(Path(%r).read_text(encoding='utf-8')), Path(%r))]\n"
            "handles = s.acquire_scope_locks(scopes, 'test claim')\n"
            "try:\n"
            "    selected = s.select_summary_request(scopes, claim=True)\n"
            "finally:\n"
            "    for handle in reversed(handles):\n"
            "        handle.close()\n"
            "print(json.dumps({'claimed': selected is not None}))\n"
            % (str(release / "scripts"), str(claim_config), str(workdir / "state")),
            encoding="utf-8",
        )
        proc = subprocess.run(["python3", str(script)], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise AssertionError(f"claim failed: {self.redact(proc.stderr)[-300:]}")

    def run_publisher(self, config: dict, workdir: Path, summary: str, facts_receipt: str) -> dict:
        """Run the fixed publisher against the TEST's own config/state via a temp manifest.

        In L2-2 the test stands in for the Desk LLM: the prose is deterministic
        and must itself satisfy the publisher's filter and facts-consistency gate.
        """

        config_path = workdir / f"config-{secrets.token_hex(4)}.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        config_path.chmod(0o600)
        manifest = {
            "version": 1,
            "release_dir": self.state.get("runner_release_dir"),
            "sync": [{"config": str(config_path), "state_dir": str(workdir / "state")}],
            "route": {"config": str(STATE_FILE.parent / "gitlab-buzz-desk-route.json"),
                      "state_dir": str(STATE_FILE.parent / "desk-route-state")},
        }
        manifest_path = workdir / "desk-runner.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_path.chmod(0o600)
        env = {"HOME": str(workdir / "home"), "PATH": "/usr/bin:/bin", "BUZZ_RELAY_URL": self.relay_http,
               "BUZZ_PRIVATE_KEY": self.desk["secret"], "BUZZ_AUTH_TAG": self.desk["auth_tag"],
               TOKEN_ENV: self.tokens["bot"], "BUZZ_DESK_RUNNER_MANIFEST": str(manifest_path)}
        script = Path(self.state["runner_release_dir"]) / "scripts" / "gitlab_buzz_summary_publish.py"
        proc = subprocess.run(
            ["python3", str(script), "--summary", summary, "--facts", facts_receipt],
            env=env, cwd=REPO, capture_output=True, text=True, timeout=300,
        )
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise AssertionError(
                f"publisher exit {proc.returncode}: {self.redact(proc.stdout or proc.stderr)[-400:]}")


def issue_header(project: int, iid: int, type_: str, status: str, change: str, state: str = "opened") -> str:
    return (f"[gitlab-notify:v1][object:issue][type:{type_}][status:{status}][state:{state}]"
            f"[change:{change}][project:{project}][issue:{iid}]")


def mr_header(project: int, iid: int, state: str, draft: str, change: str,
              transition: str = "none") -> str:
    return (f"[gitlab-notify:v1][object:mr][state:{state}][draft:{draft}][change:{change}]"
            f"[transition:{transition}][project:{project}][mr:{iid}]")


@unittest.skipUnless(ENABLED, "L2-2 runs only with BUZZ_SYNC_L2=1 and the localstack up")
class SyncLocalTest(unittest.TestCase):
    maxDiff = None
    stack: Stack

    @classmethod
    def setUpClass(cls):
        cls.stack = Stack()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.stack.homes, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bsls-l2-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.case = self._testMethodName.split("_")[1]
        # x-joined so the branch never matches the <word>-<iid>-<slug> issue-branch whitelist
        self.stamp = f"{self.case}x{secrets.token_hex(3)}"
        self.started_unix = int(time.time()) - 5
        self.since = iso(utc_now() - dt.timedelta(minutes=1))
        self.cfg = self.stack.config(self.since)

    # ── helpers ─────────────────────────────────────────────────────────────

    def sync(self, config: dict | None = None, token: str | None = None, workdir: Path | None = None,
             expect_ok: bool = True) -> SyncRun:
        run = self.stack.run_sync(config or self.cfg, workdir or self.tmp, token)
        if expect_ok:
            self.assertEqual((run.returncode, run.status), (0, "ok"), run.describe())
        self.drain_summaries(config or self.cfg, workdir or self.tmp, run)
        return run

    def drain_summaries(self, config: dict, workdir: Path, run: SyncRun) -> None:
        """Ack every durable summary request through the real publisher.

        A pending request back-pressures the next scan by design; L2-2 cases
        must drain it exactly like the Desk turn would.
        """

        for request in run.result.get("summary_requests") or []:
            self.stack.claim_oldest_summary(config, workdir)
            facts = request.get("facts") or []
            prose = f"本轮共有 {len(facts)} 条分支活动。"
            outcome = self.stack.run_publisher(
                config, workdir, prose, request.get("facts_sha256", ""),
            )
            self.assertIn(outcome.get("status"), {"sent", "duplicate"},
                          f"publisher outcome: {outcome}")

    def only_root(self, kind: str, iid: int) -> dict:
        roots = self.stack.roots(kind, iid, self.started_unix)
        self.assertEqual(len(roots), 1, f"{kind} {iid} roots: {[first_line(r) for r in roots]}")
        return roots[0]

    def facts(self, root: dict) -> list[dict]:
        """Desk facts in a Thread, including a new Issue's first fact when it is the root."""

        return [e for e in self.stack.publisher_thread(root["id"])
                if e["id"] != root["id"] or header(e).startswith("[gitlab-notify:v1][object:issue]")]

    def only_binding(self, kind: str, iid: int, root: dict) -> dict:
        bindings = self.stack.bindings(kind, iid)
        self.assertEqual(len(bindings), 1, bindings)
        binding = bindings[0]
        self.assertEqual({k: binding[k] for k in ("project_id", "object", "iid", "channel_id", "root_event_id")},
                         {"project_id": self.stack.project_id, "object": kind, "iid": iid,
                          "channel_id": self.stack.channel, "root_event_id": root["id"]})
        self.assertIn(f"buzz://message?channel={self.stack.channel}&id={root['id']}", binding["body"])
        return binding

    def assert_reply(self, event: dict, root: dict):
        self.assertEqual([t[:4] for t in tag_values(event, "e")], [["e", root["id"], "", "reply"]])

    def assert_no_agent_mentions(self, events: list[dict]):
        for event in events:
            self.assertFalse(p_tags(event) & {self.stack.desk["pubkey"], self.stack.role_pubkey}, first_line(event))

    def new_synced_issue(self, title: str, labels: str = "type::feature,status::backlog") -> tuple[dict, dict]:
        issue = self.stack.create_issue(title, labels)
        self.sync()
        return issue, self.only_root("issue", issue["iid"])

    def ready_mr(self, files: dict[str, str], title: str, reviewers=("maintainer",), extra_files=None) -> dict:
        branch = f"feature/l2-{self.stamp}"
        sha = self.stack.branch_with_files(branch, {**files, **(extra_files or {})}, f"feat: {title}")
        mr = self.stack.create_mr(branch, title, reviewers)
        wait_for("MR head sha", lambda: self.stack.mr(mr["iid"]).get("sha") == sha, 60)
        return {**mr, "sha": sha, "branch": branch}

    # ── L2-2-GIS-001..010, 013: Issues ──────────────────────────────────────

    def test_001_new_issue_root_and_binding(self):
        """L2-2-GIS-001 新 Issue → 首条 routing 事实即 root，Issue 恰好 1 条 bot binding note。"""
        issue = self.stack.create_issue(f"L2 {self.stamp} new issue @localstack-owner", "type::feature,status::ready")
        run = self.sync()
        self.assertGreaterEqual(run.result["created"], 1)
        root = self.only_root("issue", issue["iid"])
        self.assertEqual(root["kind"], 9)
        self.assertEqual([t[:2] for t in tag_values(root, "h")], [["h", self.stack.channel]])
        self.assertEqual(p_tags(root), set(), "Issue messages must not mention anyone")
        self.assertTrue(lines(root)[0].startswith("📋 **已打开** · "))
        self.assertIn(f"[#{issue['iid']} L2 {self.stamp} new issue ＠localstack-owner](", root["content"])
        self.assertRegex(root["content"], rf"/-/(issues|work_items)/{issue['iid']}")
        fact = self.facts(root)
        self.assertEqual([header(e) for e in fact],
                         [issue_header(self.stack.project_id, issue["iid"], "feature", "ready", "routing")])
        self.assertEqual(fact[0]["id"], root["id"])
        self.assertEqual(tag_values(fact[0], "e"), [])
        self.assertEqual(p_tags(fact[0]), set(), "Issue messages must not mention anyone")
        self.assertIn(f"[#{issue['iid']} L2 {self.stamp} new issue ＠localstack-owner](", lines(fact[0])[0])
        self.only_binding("issue", issue["iid"], root)
        self.assertIn(f"buzz://message?channel={self.stack.channel}&id={root['id']}&thread={root['id']}", run.result["links"])

    def test_002_rerun_is_idempotent(self):
        """L2-2-GIS-002 同步成功后立即重跑 → 频道与 GitLab 两侧零新增。"""
        issue, root = self.new_synced_issue(f"L2 {self.stamp} rerun")
        thread_before = [e["id"] for e in self.stack.thread(root["id"])]
        notes_before = [n["id"] for n in self.stack.notes("issue", issue["iid"])]
        channel_before = [e["id"] for e in self.stack.publisher_channel_messages(self.started_unix)
                          if f"[issue:{issue['iid']}]" in first_line(e)]
        self.sync()
        self.assertEqual([e["id"] for e in self.stack.thread(root["id"])], thread_before)
        self.assertEqual([n["id"] for n in self.stack.notes("issue", issue["iid"])], notes_before)
        self.assertEqual([e["id"] for e in self.stack.publisher_channel_messages(self.started_unix)
                          if f"[issue:{issue['iid']}]" in first_line(e)], channel_before)

    def test_003_label_change_routing_reply(self):
        """L2-2-GIS-003 改 status label → 原 Thread 恰好 1 条 change:routing 回帖，重跑不重复。"""
        issue, root = self.new_synced_issue(f"L2 {self.stamp} relabel")
        self.assertEqual([header(e) for e in self.facts(root)],
                         [issue_header(self.stack.project_id, issue["iid"], "feature", "backlog", "routing")])
        self.stack.ok("dev", "PUT", f"{self.stack.base}/issues/{issue['iid']}",
                      body={"add_labels": "status::ready", "remove_labels": "status::backlog"})
        self.sync()
        desk = self.facts(root)
        self.assertEqual([header(e) for e in desk], [
            issue_header(self.stack.project_id, issue["iid"], "feature", "backlog", "routing"),
            issue_header(self.stack.project_id, issue["iid"], "feature", "ready", "routing")])
        self.assert_reply(desk[1], root)
        self.assertEqual(p_tags(desk[1]), set())
        self.sync()
        self.assertEqual(len(self.facts(root)), 2)

    def test_004_comment_activity_and_bot_note_silent(self):
        """L2-2-GIS-004 bot 自己的 note → 无消息；人工评论 → 恰好 1 条 change:activity（note id 在 header trailer、正文 ＠ 已中和；评论里精确点名的已映射成员按 ADR-0018 得到 p tag），重跑不重复。

        测试方案原文「只有评论或 bot note → 无消息」已被 US-GIS-02 / L1-GIS-027 取代：评论要发 activity。
        """
        issue, root = self.new_synced_issue(f"L2 {self.stamp} comments")
        self.stack.ok("bot", "POST", f"{self.stack.base}/issues/{issue['iid']}/notes", body={"body": "l2 bot note"})
        self.sync()
        self.assertEqual(len(self.facts(root)), 1, "a bot-authored note must not produce a message")
        note = self.stack.ok("dev", "POST", f"{self.stack.base}/issues/{issue['iid']}/notes",
                             body={"body": "l2 human comment for @buzz-sync-maintainer"})
        self.sync()
        desk = self.facts(root)
        self.assertEqual(len(desk), 2, [first_line(e) for e in desk])
        reply = desk[1]
        self.assertEqual(header(reply), issue_header(self.stack.project_id, issue["iid"], "feature", "backlog", "activity"))
        self.assertIn(f"[note:{note['id']}]", first_line(reply))  # the note id lives in the header trailer
        self.assertIn(f"by: {self.stack.users['dev']['username']}", lines(reply))
        self.assertIn("l2 human comment for ＠buzz-sync-maintainer", lines(reply))
        self.assert_reply(reply, root)
        # ADR-0018: a comment that names a mapped project member exactly (`@buzz-sync-maintainer`) notifies
        # that person through the resolved pubkey; the text itself stays neutralized (＠) and adds no tag.
        self.assertEqual(p_tags(reply), {self.stack.owner["pubkey"]})
        self.sync()
        self.assertEqual(len(self.facts(root)), 2)

    def test_005_deleted_binding_note_is_recovered(self):
        """L2-2-GIS-005 删掉 binding note 后重跑 → 按 Desk root 找回并补 note，不新建 root。"""
        issue, root = self.new_synced_issue(f"L2 {self.stamp} recover")
        binding = self.only_binding("issue", issue["iid"], root)
        self.stack.ok("bot", "DELETE", f"{self.stack.base}/issues/{issue['iid']}/notes/{binding['note_id']}")
        self.assertEqual(self.stack.bindings("issue", issue["iid"]), [])
        run = self.sync()
        self.assertGreaterEqual(run.result["recovered"], 1, run.describe())
        self.only_root("issue", issue["iid"])
        self.only_binding("issue", issue["iid"], root)

    def test_005b_deleted_mr_binding_note_is_recovered(self):
        """L2-2-GIS-005b 删掉 MR 的 binding note 后重跑 → 按 MR 门牌 URL 路径尾找回并补 note，不新建 root（#106）。"""
        mr = self.ready_mr({f"src/l2_{self.stamp}.py": "print(1)\n"}, f"L2 {self.stamp} mr recover")
        iid = mr["iid"]
        self.sync()
        root = self.only_root("mr", iid)
        binding = self.only_binding("mr", iid, root)
        self.stack.ok("bot", "DELETE", f"{self.stack.base}/merge_requests/{iid}/notes/{binding['note_id']}")
        self.assertEqual(self.stack.bindings("mr", iid), [])
        run = self.sync()
        self.assertGreaterEqual(run.result["recovered"], 1, run.describe())
        self.only_root("mr", iid)
        self.only_binding("mr", iid, root)

    def test_006_deleted_cache_changes_nothing(self):
        """L2-2-GIS-006 删除本地缓存（0600）后重跑 → 结果不变。"""
        issue, root = self.new_synced_issue(f"L2 {self.stamp} cache")
        caches = list((self.tmp / "state").glob("gitlab-buzz-sync-*.cache.json"))
        self.assertEqual(len(caches), 1)
        self.assertEqual(stat.S_IMODE(caches[0].stat().st_mode), 0o600)
        thread_before = [e["id"] for e in self.stack.thread(root["id"])]
        caches[0].unlink()
        self.sync()
        self.assertEqual([e["id"] for e in self.stack.thread(root["id"])], thread_before)
        self.only_root("issue", issue["iid"])
        self.only_binding("issue", issue["iid"], root)

    def test_007_bad_tokens_write_nothing(self):
        """L2-2-GIS-007 无效 token、身份不符的 token、读不到 project 的 token → 非零退出、stdout 不回显 token、两侧零写入。"""
        issue = self.stack.create_issue(f"L2 {self.stamp} bad token")
        before = [e["id"] for e in self.stack.publisher_channel_messages(self.started_unix)]
        invalid = "glpat-" + secrets.token_hex(10)
        outsider = self.stack.users["outsider"]
        runs = {
            "invalid": self.stack.run_sync(self.cfg, self.tmp / "invalid", invalid),
            "identity": self.stack.run_sync(self.cfg, self.tmp / "identity", self.stack.tokens["outsider"]),
            "project": self.stack.run_sync(
                self.stack.config(self.since, gitlab={"bot_user_id": outsider["id"], "bot_username": outsider["username"]}),
                self.tmp / "project", self.stack.tokens["outsider"]),
        }
        for name, run in runs.items():
            with self.subTest(name):
                self.assertEqual((run.returncode, run.status), (1, "error"), run.describe())
                self.assertNotIn(invalid, run.stdout + run.stderr)
                self.assertNotIn("glpat-", run.stdout)
        self.assertIn("identity", runs["identity"].result.get("error", ""))
        self.assertIn("404", runs["project"].result.get("error", ""))
        self.assertEqual([e["id"] for e in self.stack.publisher_channel_messages(self.started_unix)], before)
        self.assertEqual(self.stack.bindings("issue", issue["iid"]), [])
        self.assertEqual(self.stack.notes("issue", issue["iid"]), [])

    def test_008_confidential_and_excluded_are_skipped(self):
        """L2-2-GIS-008 confidential Issue（默认）与命中 exclude（label / assignee）的 Issue → 跳过并计数，两侧零写入。"""
        confidential = self.stack.create_issue(f"L2 {self.stamp} confidential", confidential=True)
        by_label = self.stack.create_issue(f"L2 {self.stamp} excluded label", "type::bug,status::ready,l2-exclude")
        by_assignee = self.stack.create_issue(f"L2 {self.stamp} excluded assignee",
                                              assignee_ids=[self.stack.users["maintainer"]["id"]])
        config = self.stack.config(self.since, exclude=[{"label": "l2-exclude"},
                                                        {"assignee_username": self.stack.users["maintainer"]["username"]}])
        run = self.sync(config)
        self.assertGreaterEqual(run.result["skipped"]["confidential"], 1, run.describe())
        self.assertGreaterEqual(run.result["skipped"]["excluded"], 2, run.describe())
        for issue in (confidential, by_label, by_assignee):
            with self.subTest(issue["title"]):
                self.assertEqual(self.stack.roots("issue", issue["iid"], self.started_unix), [])
                self.assertEqual(self.stack.bindings("issue", issue["iid"]), [])

    def test_009_title_only_content_reply(self):
        """L2-2-GIS-009 只改标题 → 原 Thread 恰好 1 条 change:content 回帖，重跑不重复。"""
        issue, root = self.new_synced_issue(f"L2 {self.stamp} title")
        self.stack.ok("dev", "PUT", f"{self.stack.base}/issues/{issue['iid']}", body={"title": f"L2 {self.stamp} title renamed"})
        self.sync()
        desk = self.facts(root)
        self.assertEqual(len(desk), 2, [first_line(e) for e in desk])
        self.assertEqual(header(desk[1]), issue_header(self.stack.project_id, issue["iid"], "feature", "backlog", "content"))
        self.assertIn(f"[#{issue['iid']} L2 {self.stamp} title renamed](", lines(desk[1])[0])
        self.assert_reply(desk[1], root)
        self.sync()
        self.assertEqual(len(self.facts(root)), 2)

    def test_010_concurrent_runs_single_writer(self):
        """L2-2-GIS-010 同频道同 state-dir 并发两次 → 一个 locked、一个写入；恰好 1 root + 1 binding。"""
        issue = self.stack.create_issue(f"L2 {self.stamp} concurrent")
        procs = []
        for _ in range(2):
            args, env = self.stack.sync_command(self.cfg, self.tmp)
            procs.append(subprocess.Popen(args, env=env, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True))
        runs = []
        for proc in procs:
            out, err = proc.communicate(timeout=900)
            runs.append(SyncRun(proc.returncode, self.stack.redact(out), self.stack.redact(err)))
        self.assertEqual(sorted(str(r.status) for r in runs), ["locked", "ok"], [r.describe() for r in runs])
        self.assertTrue(all(r.returncode == 0 for r in runs), [r.describe() for r in runs])
        root = self.only_root("issue", issue["iid"])
        self.only_binding("issue", issue["iid"], root)

    def test_013_issue_before_since_is_not_backfilled(self):
        """L2-2-GIS-013 创建早于 since 且未绑定的 Issue（since 之后又被更新）→ 计 backfill，不建 root、不写 note。"""
        issue = self.stack.create_issue(f"L2 {self.stamp} backfill")
        since = parse_iso(issue["created_at"]).replace(microsecond=0) + dt.timedelta(seconds=1)
        wait_for("GitLab clock past since", lambda: self.stack.server_time() > since + dt.timedelta(seconds=1), 30)
        self.stack.ok("dev", "PUT", f"{self.stack.base}/issues/{issue['iid']}", body={"add_labels": "status::ready"})
        run = self.sync(self.stack.config(iso(since)))
        self.assertGreaterEqual(run.result["skipped"]["backfill"], 1, run.describe())
        self.assertEqual(self.stack.roots("issue", issue["iid"], self.started_unix), [])
        self.assertEqual(self.stack.bindings("issue", issue["iid"]), [])

    # ── L2-2-GIS-011, 012, 014, 015, 017: merge requests ────────────────────

    def test_011_mr_diff_per_file_once_per_sha(self):
        """L2-2-GIS-011 diff.enabled + diff.private → 按文件发 kind 40008 进 MR Thread，同 head sha 重跑不重发。"""
        files = {f"src/l2_{self.stamp}_a.py": "print('a')\n", f"docs/l2_{self.stamp}_b.md": "# b\n"}
        mr = self.ready_mr(files, f"L2 {self.stamp} diff")
        wait_for("MR diffs", lambda: len(self.stack.paged("bot", f"{self.stack.base}/merge_requests/{mr['iid']}/diffs")) == 2)
        config = self.stack.config(self.since, diff={"enabled": True, "private": True})
        self.sync(config)
        root = self.only_root("mr", mr["iid"])
        diffs = self.stack.publisher_thread(root["id"], kind=40008)
        self.assertEqual(sorted(tag[1] for e in diffs for tag in tag_values(e, "file")), sorted(files))
        for event in diffs:
            self.assertIn(["commit", mr["sha"]], event["tags"])
            self.assert_reply(event, root)
        self.sync(config)
        self.assertEqual([e["id"] for e in self.stack.publisher_thread(root["id"], kind=40008)], [e["id"] for e in diffs])

    def test_012_private_project_without_diff_private_sends_no_diff(self):
        """L2-2-GIS-012 private 项目、diff.enabled 但未开 diff.private → MR root 照发，不发 Diff。"""
        mr = self.ready_mr({f"src/l2_{self.stamp}.py": "print(1)\n"}, f"L2 {self.stamp} no diff")
        wait_for("MR diffs", lambda: self.stack.paged("bot", f"{self.stack.base}/merge_requests/{mr['iid']}/diffs"))
        self.sync(self.stack.config(self.since, diff={"enabled": True, "private": False}))
        root = self.only_root("mr", mr["iid"])
        self.assertEqual(self.stack.publisher_thread(root["id"], kind=40008), [])

    def test_014_mr_root_update_and_merge(self):
        """L2-2-GIS-014 新建 MR → 🔀 门牌 root + 首条 lifecycle 事实 + MR binding note；推新提交 → 1 条 update；合并 → 1 条 lifecycle。"""
        path = f"src/l2_{self.stamp}.py"
        mr = self.ready_mr({path: "print(1)\n"}, f"L2 {self.stamp} lifecycle")
        pid, iid = self.stack.project_id, mr["iid"]
        self.sync()
        root = self.only_root("mr", iid)
        self.assertEqual(lines(root)[0], f"🔀 **!{iid} L2 {self.stamp} lifecycle**")
        first = self.facts(root)
        self.assertEqual([header(e) for e in first], [mr_header(pid, iid, "opened", "no", "lifecycle", "reviewable")])
        self.assertIn(f"sha {mr['sha'][:12]}", "\n".join(lines(first[0])))
        self.assert_reply(first[0], root)
        self.only_binding("mr", iid, root)

        new_sha = self.stack.commit(mr["branch"], {path: "print(2)\n"}, "fix: second commit")
        wait_for("MR head sha", lambda: self.stack.mr(iid).get("sha") == new_sha, 60)
        self.sync()
        desk = self.facts(root)
        self.assertEqual([header(e) for e in desk][1:], [mr_header(pid, iid, "opened", "no", "update")])
        self.assertIn(f"sha {new_sha[:12]}", "\n".join(lines(desk[1])))
        self.assert_reply(desk[1], root)

        # detailed_merge_status stays `unchecked` until someone who may merge asks GitLab to recheck it.
        wait_for("MR mergeable", lambda: self.stack.ok("maintainer", "GET", f"{self.stack.base}/merge_requests/{iid}",
                                                       {"with_merge_status_recheck": "true"})
                 .get("detailed_merge_status") == "mergeable", 90)
        self.stack.ok("maintainer", "PUT", f"{self.stack.base}/merge_requests/{iid}/merge")
        wait_for("MR merged", lambda: self.stack.mr(iid).get("state") == "merged", 90)
        self.sync()
        desk = self.facts(root)
        lifecycle = [e for e in desk if header(e) == mr_header(pid, iid, "merged", "no", "lifecycle")]
        self.assertEqual(len(lifecycle), 1, [first_line(e) for e in desk])
        self.assert_no_agent_mentions(desk)
        count = len(desk)
        self.sync()
        self.assertEqual(len(self.facts(root)), count)

    def test_015_failed_mr_pipeline_activity_with_job(self):
        """L2-2-GIS-015 MR 流水线失败 → MR Thread 恰好 1 条 activity 回帖（event: pipeline failed、jobs: test:unit），重跑不重复。"""
        mr = self.ready_mr({f"src/l2_{self.stamp}.py": "print(1)\n"}, f"L2 {self.stamp} pipeline",
                           extra_files={".gitlab-ci.yml": CI_YAML} if not self._main_has_ci() else None)
        pid, iid = self.stack.project_id, mr["iid"]
        pipeline = wait_for("MR pipeline", lambda: next(iter(
            self.stack.ok("bot", "GET", f"{self.stack.base}/merge_requests/{iid}/pipelines") or []), None), 90)
        self.sync()
        root = self.only_root("mr", iid)
        dropped = self.stack.rails_drop_pipeline(pipeline["id"])
        self.assertEqual(dropped["dropped"], ["test:unit"], dropped)
        # GitLab recomputes the pipeline status asynchronously after a job is dropped.
        detail = wait_for("pipeline failed status", lambda: (lambda d: d if d.get("status") == "failed" else None)(
            self.stack.ok("bot", "GET", f"{self.stack.base}/pipelines/{pipeline['id']}")), 120, 2)
        self.assertEqual(detail["ref"], f"refs/merge-requests/{iid}/head")
        self.sync()
        activity = [e for e in self.facts(root) if header(e) == mr_header(pid, iid, "opened", "no", "activity")]
        self.assertEqual(len(activity), 1, [first_line(e) for e in self.facts(root)])
        self.assertIn("流水线失败", lines(activity[0])[0])
        self.assertIn("jobs: test:unit", lines(activity[0]))
        self.assertIn(f"pipeline-{pipeline['id']}-failed", event_keys(activity[0]))  # dedup key: header trailer
        self.assert_reply(activity[0], root)
        self.sync()
        self.assertEqual(len([e for e in self.facts(root)
                              if header(e) == mr_header(pid, iid, "opened", "no", "activity")]), 1)

    def _main_has_ci(self) -> bool:
        status, _, _ = self.stack.api("bot", "GET", f"{self.stack.base}/repository/files/.gitlab-ci.yml", {"ref": "main"})
        return status == 200

    def test_017_mr_mentions_mapped_people_only(self):
        """L2-2-GIS-017 非 draft MR root 恰好 @ 映射的 reviewer∪Maintainer（排除作者）；换新 reviewer → 只 @ 新人；标题 @ 不加 p tag。

        GitLab CE 每个 MR 只能有一个 reviewer（UpdateReviewersService 取 first(1)），「再加一个」按替换执行。
        """
        human = self._fresh_member()
        maintainer, bot = self.stack.users["maintainer"], self.stack.users["bot"]
        config = self.stack.config(
            self.since,
            people={maintainer["username"]: human, bot["username"]: self.stack.owner["pubkey"]},
        )
        # `@localstack-owner` is the owner's Buzz display name: resolved, it would add the owner as an extra p tag.
        mr = self.ready_mr({f"src/l2_{self.stamp}.py": "print(1)\n"}, f"L2 {self.stamp} ping @localstack-owner")
        pid, iid = self.stack.project_id, mr["iid"]
        self.sync(config)
        root = self.only_root("mr", iid)
        first = self.facts(root)[0]  # the reviewable lifecycle fact carries the mentions, not the plaque
        self.assertEqual(p_tags(root), set())
        self.assertEqual(p_tags(first), {human})
        self.assertIn(f"[!{iid} L2 {self.stamp} ping ＠localstack-owner](", lines(first)[0])
        unmapped = next((ln for ln in lines(first) if ln.startswith("unmapped: ")), "")
        self.assertIn("root(profile_not_found)", unmapped.removeprefix("unmapped: ").split(","))

        self.stack.ok("dev", "PUT", f"{self.stack.base}/merge_requests/{iid}", body={"reviewer_ids": [bot["id"]]})
        wait_for("reviewer change", lambda: [r["username"] for r in self.stack.mr(iid).get("reviewers") or []] == [bot["username"]], 60)
        self.sync(config)
        desk = self.facts(root)
        self.assertEqual([header(e) for e in desk][1:], [mr_header(pid, iid, "opened", "no", "update")])
        self.assertEqual(p_tags(desk[1]), {self.stack.owner["pubkey"]})
        self.assertTrue(any(f"reviewers {bot['username']}" in line.split(" · ") for line in lines(desk[1])))
        self.assert_no_agent_mentions(desk)

    def test_018_reopened_mr_mentions_full_set(self):
        """L2-2-GIS-018 第一次同步时已关闭的 MR：root 不 @；重开后 lifecycle 回帖 @ 映射的 reviewer∪Maintainer（排除作者）。"""
        human = self._fresh_member()
        config = self.stack.config(
            self.since,
            people={self.stack.users["maintainer"]["username"]: human},
        )
        mr = self.ready_mr({f"src/l2_{self.stamp}.py": "print(1)\n"}, f"L2 {self.stamp} reopen")
        pid, iid = self.stack.project_id, mr["iid"]
        self.stack.ok("dev", "PUT", f"{self.stack.base}/merge_requests/{iid}", body={"state_event": "close"})
        wait_for("MR closed", lambda: self.stack.mr(iid).get("state") == "closed", 60)
        self.sync(config)
        root = self.only_root("mr", iid)
        closed = self.facts(root)
        self.assertEqual([header(e) for e in closed], [mr_header(pid, iid, "closed", "no", "lifecycle")])
        self.assertEqual(p_tags(root) | p_tags(closed[0]), set())
        self.stack.ok("dev", "PUT", f"{self.stack.base}/merge_requests/{iid}", body={"state_event": "reopen"})
        wait_for("MR reopened", lambda: self.stack.mr(iid).get("state") == "opened", 60)
        self.sync(config)
        replies = [e for e in self.facts(root)
                   if header(e) == mr_header(pid, iid, "opened", "no", "lifecycle", "reviewable")]
        self.assertEqual(len(replies), 1)
        self.assertEqual(p_tags(replies[0]), {human})

    def _people_file(self, entries: dict[str, str], mode: int = 0o600) -> Path:
        path = self.tmp / f"people-{secrets.token_hex(3)}.json"
        path.write_text(json.dumps(entries), encoding="utf-8")
        path.chmod(mode)
        return path

    def test_pf1_mr_mentions_via_people_file(self):
        """L2-2-GIS-PF-001 people 只在 people_file 里：MR 可评审时 p-tag 恰好是映射的 reviewer。"""
        human = self._fresh_member()
        maintainer = self.stack.users["maintainer"]
        mr = self.ready_mr({f"src/l2_{self.stamp}.py": "print(1)\n"}, f"L2 {self.stamp} people-file")
        shared = self._people_file({maintainer["username"]: human})
        config = self.stack.config(self.since, people_file=str(shared))
        config.pop("people")  # the channel config carries no inline people at all
        self.sync(config)
        root = self.only_root("mr", mr["iid"])
        first = self.facts(root)[0]  # mentions ride on the first (reviewable) fact, the plaque carries none
        self.assertEqual(p_tags(root), set())
        self.assertEqual(p_tags(first), {human})
        self.assert_no_agent_mentions([root, first])

    def test_pf3_inline_people_override_the_shared_entry(self):
        """L2-2-GIS-PF-003 内联 people 覆盖 people_file 的同名条目：@ 的是内联那个 pubkey，共享文件里的旧值不出现。"""
        human, other = self._fresh_member(), self._fresh_member()
        maintainer = self.stack.users["maintainer"]
        mr = self.ready_mr({f"src/l2_{self.stamp}.py": "print(1)\n"}, f"L2 {self.stamp} people-file-override")
        shared = self._people_file({maintainer["username"]: human})
        config = self.stack.config(self.since, people_file=str(shared), people={maintainer["username"]: other})
        self.sync(config)
        root = self.only_root("mr", mr["iid"])
        first = self.facts(root)[0]
        self.assertEqual(p_tags(first), {other})
        self.assertNotIn(human, p_tags(first) | p_tags(root))

    def test_pf2_bad_people_file_refuses_to_load_and_sends_nothing(self):
        """L2-2-GIS-PF-002 people_file 缺失或权限过宽：sync 拒绝加载（status=error），Buzz 里没有任何新消息。"""
        mr = self.ready_mr({f"src/l2c_{self.stamp}.py": "print(3)\n"}, f"L2 {self.stamp} people-file-bad")
        for label, shared in (("missing", self.tmp / "absent.json"),
                              ("wide", self._people_file({"x": self.stack.owner["pubkey"]}, mode=0o644))):
            with self.subTest(label=label):
                config = self.stack.config(self.since, people_file=str(shared))
                run = self.sync(config, expect_ok=False)
                self.assertNotEqual(run.returncode, 0, run.describe())
                self.assertEqual(run.status, "error", run.describe())
                self.assertIn("people_file", run.result.get("error", ""), run.describe())
        self.assertEqual(self.stack.roots("mr", mr["iid"], self.started_unix), [])

    NOTIFIED = "🔔 通知 "

    def _notified(self, event: dict) -> list[str]:
        return [line for line in lines(event) if line.startswith(self.NOTIFIED)]

    def test_nl1_notified_line_names_who_was_mentioned_and_keeps_the_p_tags_exact(self):
        """L2-2-GIS-NL-001 带 @ 的 MR 消息在正文多一行「🔔 通知」：有档案显示名的成员写成真正的 @显示名，没有档案的退回纯用户名；
        回读的 p tag 恰为预期（CLI 不因正文里的 @显示名多出别人），机器头部行仍是最后一行。"""
        human = self._fresh_member()  # a fresh member has no profile → no display name to mention
        maintainer, bot = self.stack.users["maintainer"], self.stack.users["bot"]
        config = self.stack.config(
            self.since,
            people={maintainer["username"]: human, bot["username"]: self.stack.owner["pubkey"]},
        )
        mr = self.ready_mr({f"src/l2nl_{self.stamp}.py": "print(1)\n"}, f"L2 {self.stamp} notified-line")
        iid = mr["iid"]
        self.sync(config)
        root = self.only_root("mr", iid)
        first = self.facts(root)[0]
        self.assertEqual(p_tags(root), set())
        self.assertEqual(self._notified(root), [])  # the plaque carries no mention, so no line either
        self.assertEqual(p_tags(first), {human})
        self.assertEqual(self._notified(first), [self.NOTIFIED + maintainer["username"]])  # plain name, no @
        self.assertTrue(lines(first)[-1].startswith("[gitlab-notify:v1]"))

        self.stack.ok("dev", "PUT", f"{self.stack.base}/merge_requests/{iid}", body={"reviewer_ids": [bot["id"]]})
        wait_for("reviewer change", lambda: [r["username"] for r in self.stack.mr(iid).get("reviewers") or []] == [bot["username"]], 60)
        self.sync(config)
        desk = self.facts(root)
        # the owner's Buzz display name is `localstack-owner`: a real @ mention in the body, the p tag set still exact
        self.assertEqual(p_tags(desk[1]), {self.stack.owner["pubkey"]})
        self.assertEqual(self._notified(desk[1]), [self.NOTIFIED + "@localstack-owner"])
        self.assertTrue(lines(desk[1])[-1].startswith("[gitlab-notify:v1]"))
        self.assert_no_agent_mentions(desk)

    def test_nl2_messages_without_a_mention_get_no_line(self):
        """L2-2-GIS-NL-002 没有 p tag 的消息（MR 门牌、没有映射 reviewer 的可评审事实、Issue 状态卡）都不加「🔔 通知」行。"""
        mr = self.ready_mr({f"src/l2nl2_{self.stamp}.py": "print(2)\n"}, f"L2 {self.stamp} no-mention")
        config = self.stack.config(self.since, people={})
        self.sync(config)
        root = self.only_root("mr", mr["iid"])
        for event in [root, *self.facts(root)]:
            self.assertEqual(p_tags(event), set())
            self.assertEqual(self._notified(event), [])

    def _fresh_member(self) -> str:
        sys.path.insert(0, str(REF_SCRIPTS))
        try:
            import nostrkit as nk  # noqa: PLC0415 - repo helper, only needed by this case
        finally:
            sys.path.remove(str(REF_SCRIPTS))
        while True:
            key = secrets.token_bytes(32)
            if 1 <= int.from_bytes(key, "big") < nk.n:
                break
        pubkey = nk.pubkey_xonly(key).hex()  # the secret is discarded: this member never signs anything
        self.stack.ensure_member(pubkey)
        self.addCleanup(
            self.stack.buzz, "owner", "channels", "remove-member",
            "--channel", self.stack.channel, "--pubkey", pubkey,
        )
        return pubkey

    # ── L2-2-GIS-016: top-level instant and digest ─────────────────────────

    def test_016_push_tag_and_branch_delete_notify_once(self):
        """L2-2-GIS-016 本地新建分支+push+删分支、打 tag → tag 恰好 1 条即时；分支 push/删除不再产生任何消息或摘要请求（2026-09-18 政策）；重跑与删缓存重跑都不重复。"""
        branch, tag = f"l2-{self.stamp}", f"l2-v{self.stamp}"
        self.stack.branch_with_files(branch, {f"notes/l2_{self.stamp}.md": "x\n"}, "chore: l2 push")
        self.stack.ok("dev", "DELETE", f"{self.stack.base}/repository/branches/{urllib.parse.quote(branch, safe='')}")
        self.stack.ok("dev", "POST", f"{self.stack.base}/repository/tags", body={"tag_name": tag, "ref": "main"})
        after = (utc_now() - dt.timedelta(days=1)).date().isoformat()

        def our_events():
            events = self.stack.paged("bot", f"{self.stack.base}/events", {"after": after, "sort": "asc"})
            mine = [e for e in events if (e.get("push_data") or {}).get("ref") in (branch, tag)]
            return mine if len(mine) >= 4 else None

        events = wait_for("push/tag events", our_events, 60)
        tag_keys = {f"event-{e['id']}" for e in events if e["push_data"]["ref_type"] == "tag"}
        branch_keys = {f"event-{e['id']}" for e in events if e["push_data"]["ref_type"] == "branch"}
        self.assertEqual((len(tag_keys), len(branch_keys)), (1, 3), [(e["action_name"], e["push_data"]) for e in events])
        workdir = self.tmp / "top"
        run = self.sync(workdir=workdir)

        def carrying(keys):
            return [e for e in self.stack.publisher_channel_messages(self.started_unix) if event_keys(e) & keys]

        # 2026-09-18 policy (NOTIFIED_OBJECTS): the tag stays one instant message; branch pushes and
        # deletions produce no record at all, so there is no summary request and no digest either.
        instant = carrying(tag_keys)
        self.assertEqual(len(instant), 1, [e["content"] for e in instant])
        self.assertEqual(header(instant[0]), f"[gitlab-notify:v1][object:tag][event:tag_created][project:{self.stack.project_id}]")
        self.assertEqual(run.result.get("summary_requests") or [], [])
        self.assertEqual(carrying(branch_keys), [], "branch push/delete events must not reach the channel")
        self.assertFalse([e for e in self.stack.publisher_channel_messages(self.started_unix)
                          if "[event:digest]" in first_line(e)])
        self.assertEqual(tag_values(instant[0], "e"), [])
        self.assertEqual(p_tags(instant[0]), set())
        seen = sorted(e["id"] for e in instant)
        self.sync(workdir=workdir)
        self.assertEqual(sorted(e["id"] for e in carrying(tag_keys | branch_keys)), seen)
        for cache in (workdir / "state").glob("gitlab-buzz-sync-*.cache.json"):
            cache.unlink()
        self.sync(workdir=workdir)
        self.assertEqual(sorted(e["id"] for e in carrying(tag_keys | branch_keys)), seen)


    # ── L2-2-GIS-019..022: MR→Issue 归并（2026-09-17 决策） ─────────────────

    def merged_mr_flow(self, issue, mr_title, branch, description=""):
        sha = self.stack.branch_with_files(branch, {f"notes/m_{self.stamp}.txt": "m\n"}, f"feat: {mr_title}")
        mr = self.stack.create_mr(branch, mr_title, (), description)
        wait_for("MR head sha", lambda: self.stack.mr(mr["iid"]).get("sha") == sha, 60)
        return mr

    def mr_facts_in_thread(self, root_event, mr_iid):
        root_id = root_event["id"] if isinstance(root_event, dict) else root_event
        return [e for e in self.stack.publisher_thread(root_id)
                if f"[mr:{mr_iid}]" in first_line(e) and e["id"] != root_id]

    def test_019_mr_closing_issue_merges_into_issue_thread(self):
        """L2-2-GIS-019 Closes #i 的 MR：不开 MR root，事实进 Issue thread 且 binding 绑 issue root；重跑不重复。"""
        issue = self.stack.create_issue(f"merge source {self.stamp}")
        branch = f"feature/{self.stamp}-closes"
        mr = self.merged_mr_flow(issue, f"MR closes {self.stamp}", branch,
                                 description=f"Closes #{issue['iid']}")
        self.sync()
        issue_root = self.only_root("issue", issue["iid"])
        roots = self.stack.roots("mr", mr["iid"], self.started_unix)
        self.assertEqual(roots, [], "associated MR must not open its own root")
        facts = self.mr_facts_in_thread(issue_root, mr["iid"])
        self.assertEqual(len(facts), 1)
        binding = self.only_binding("mr", mr["iid"], issue_root)
        self.assertEqual(binding["root_event_id"], issue_root["id"])

        before = len(self.stack.publisher_thread(issue_root["id"]))
        self.sync()
        self.assertEqual(len(self.stack.publisher_thread(issue_root["id"])), before)

    def test_020_branch_name_whitelist_merges_without_closes(self):
        """L2-2-GIS-020 分支名 <iid>-<slug> 关联：无 closes 也归并；纯数字 stamp 分支不关联。"""
        issue = self.stack.create_issue(f"branch link {self.stamp}")
        branch = f"feature/{issue['iid']}-video-search"
        mr = self.merged_mr_flow(issue, f"MR branch-link {self.stamp}", branch)
        self.sync()
        issue_root = self.only_root("issue", issue["iid"])
        self.assertEqual(self.stack.roots("mr", mr["iid"], self.started_unix), [])
        self.assertEqual(len(self.mr_facts_in_thread(issue_root, mr["iid"])), 1)

    def test_021_two_mrs_one_issue_keep_disjoint_subchains(self):
        """L2-2-GIS-021 同一 Issue 两个关联 MR：首条 reply-to issue root，后续 update reply-to 各自上一条，互不穿插。"""
        issue = self.stack.create_issue(f"two mrs {self.stamp}")
        first = self.merged_mr_flow(issue, f"MR A {self.stamp}", f"feature/{issue['iid']}-alpha")
        second = self.merged_mr_flow(issue, f"MR B {self.stamp}", f"feature/{issue['iid']}-bravo")
        self.sync()
        root = self.only_root("issue", issue["iid"])
        for mr in (first, second):
            facts = self.mr_facts_in_thread(root, mr["iid"])
            self.assertEqual(len(facts), 1)
            self.assertEqual(tag_values(facts[0], "e")[0][1], root["id"])

        # push a new commit to MR A: its update chains onto MR A's own first fact
        self.stack.commit(f"feature/{issue['iid']}-alpha",
                          {f"notes/a_{self.stamp}.txt": "a2\n"}, "feat: second commit", action="create")
        wait_for("MR A sha moved", lambda: self.stack.mr(first["iid"]).get("sha") != first["sha"], 60)
        self.sync()
        a_facts = self.mr_facts_in_thread(root, first["iid"])
        self.assertEqual(len(a_facts), 2)
        reply_parent = next(tag[1] for tag in tag_values(a_facts[1], "e") if tag[3] == "reply")
        self.assertEqual(reply_parent, a_facts[0]["id"])
        self.assertEqual(len(self.mr_facts_in_thread(root, second["iid"])), 1)

    def test_022_unassociated_mr_and_existing_binding_stay_per_mr(self):
        """L2-2-GIS-022 无关联 MR 开 per-MR root；已绑定后再出现 closes 不迁移。"""
        issue = self.stack.create_issue(f"late link {self.stamp}")
        mr = self.merged_mr_flow(issue, f"MR plain {self.stamp}", f"feature/plain-{self.stamp}")
        self.sync()
        mr_root = self.only_root("mr", mr["iid"])

        self.stack.ok("dev", "PUT", f"{self.stack.base}/merge_requests/{mr['iid']}",
                      body={"description": f"Closes #{issue['iid']}"})
        wait_for("MR description updated",
                 lambda: self.stack.mr(mr["iid"]).get("description") == f"Closes #{issue['iid']}", 60)
        self.sync()
        replies = [e for e in self.stack.publisher_thread(mr_root["id"]) if e["id"] != mr_root["id"]]
        self.assertTrue(replies, "the update must land in the existing MR thread")
        for reply in replies:
            self.assertEqual(tag_values(reply, "e")[0][1], mr_root["id"])


if __name__ == "__main__":
    unittest.main()
