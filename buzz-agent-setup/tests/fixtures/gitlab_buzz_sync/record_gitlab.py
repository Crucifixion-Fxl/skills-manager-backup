#!/usr/bin/env python3
"""Record real GitLab 18.0 responses and project-webhook payloads for L2-1-GIS-003.

Local only. Reads the localstack state written by tests/localstack/stack.py, creates throwaway objects in
the localstack project (issue, comments, branch, commits, MR with reviewer and approval, external commit
statuses, MR pipeline, tag, release, deployment, milestone, wiki page, feature flag, expiring project
access token, task work item), then records the API responses gitlab_buzz_sync.py reads and, with
--webhooks, the project-webhook payloads GitLab delivers for the same changes.

Everything written is scrubbed: tokens, e-mail addresses, avatar URLs and the LAN host. Secrets are
read from 0600 files and never printed. The webhook receiver listens on the docker gateway of the GitLab
container only while recording; the hook and temporary project access token are deleted and the
instance setting is restored afterwards, including failure paths.

usage: record_gitlab.py [--out DIR] [--webhooks]
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import http.server
import ipaddress
import json
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE.parent.parent / "localstack" / ".state" / "state.json"
GITLAB_CONTAINER = "docker-gitlab-1"
LAN_HOST_RE = re.compile(r"192\.168\.\d{1,3}\.\d{1,3}")
SCRUBBED_HOST = "gitlab.localstack.test"
EMAIL_RE = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+")
TOKEN_RE = re.compile(r"glpat-[A-Za-z0-9._-]+")
EMAIL_KEYS = {"email", "public_email", "commit_email", "author_email", "committer_email", "user_email",
              "service_desk_address"}
AVATAR_KEYS = {"avatar_url", "user_avatar"}
SECRET_KEYS = {"token", "private_token", "runners_token", "password", "secret", "secret_token",
               "token_digest", "encrypted_token"}
KEEP_HEADERS = ("x-next-page", "x-page", "x-per-page", "x-total", "x-total-pages", "content-type")
EXPECTED_WEBHOOK_KINDS = {"push", "tag_push", "issue", "note", "merge_request", "pipeline", "wiki_page",
                          "deployment", "release", "feature_flag", "access_token", "work_item"}
CI_YAML = """test:unit:
  stage: test
  script: ["exit 1"]
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
      when: manual
"""

RUBY_DROP_AND_TOKEN_HOOK = r"""
require 'json'
project = Project.find(ENV.fetch('BSLS_PROJECT_ID').to_i)
out = { 'dropped' => [], 'pipelines' => {}, 'token_hook' => nil }
ENV.fetch('BSLS_DROP_PIPELINES', '').split(',').reject(&:empty?).each do |pid|
  pipeline = project.all_pipelines.find(pid.to_i)
  pipeline.builds.each do |build|
    next if build.complete?
    begin
      build.drop!(:script_failure)
    rescue StandardError
      build.enqueue! if build.respond_to?(:enqueue!) && build.can_enqueue?
      build.drop!(:script_failure)
    end
    out['dropped'] << { 'id' => build.id, 'name' => build.name, 'status' => build.reload.status }
  end
  out['pipelines'][pid] = pipeline.reload.status
end
name = ENV.fetch('BSLS_TOKEN_NAME', '')
unless name.empty?
  token = PersonalAccessToken.active.find_by!(name: name)
  raise 'token user is not this project bot' unless token.user.project_bot? && token.user.resource_bot_resource == project
  data = Gitlab::DataBuilder::ResourceAccessTokenPayload.build(token, :expiring, project, { interval: :seven_days })
  project.execute_hooks(data, :resource_access_token_hooks)
  out['token_hook'] = { 'token_id' => token.id, 'active_hooks' => project.has_active_hooks?(:resource_access_token_hooks) }
end
puts 'BSLS_JSON=' + JSON.generate(out)
"""

RUBY_ALLOW_LOCAL = r"""
s = ApplicationSetting.current
prev = s.allow_local_requests_from_web_hooks_and_services
want = ENV.fetch('BSLS_ALLOW_LOCAL') == 'true'
s.update!(allow_local_requests_from_web_hooks_and_services: want) unless prev == want
puts "BSLS_JSON={\"previous\":#{prev},\"now\":#{ApplicationSetting.current.allow_local_requests_from_web_hooks_and_services}}"
"""


class RecordError(Exception):
    pass


def log(message: str) -> None:
    print(f"[record] {message}", file=sys.stderr, flush=True)


# ── scrubbing ────────────────────────────────────────────────────────────────


def scrub(value, secrets_seen: list[str], key: str = ""):
    if isinstance(value, dict):
        return {k: scrub(v, secrets_seen, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(item, secrets_seen, key) for item in value]
    if isinstance(value, str):
        if key in AVATAR_KEYS:
            return None
        if key in EMAIL_KEYS and value:
            return "[REDACTED]"
        if key.lower() in SECRET_KEYS and value:
            return "[REDACTED]"
        for secret in secrets_seen:
            value = value.replace(secret, "[REDACTED]")
        value = TOKEN_RE.sub("glpat-[REDACTED]", value)
        value = EMAIL_RE.sub("[REDACTED]", value)
        return LAN_HOST_RE.sub(SCRUBBED_HOST, value)
    return value


# ── local GitLab API ─────────────────────────────────────────────────────────


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


class GitLab:
    def __init__(self, state: dict):
        gitlab = state["gitlab"]
        base = gitlab["base_url"]
        host = urllib.parse.urlsplit(base).hostname or ""
        if not ipaddress.ip_address(host).is_loopback:
            raise RecordError("GitLab base_url must be loopback")
        self.api = base.rstrip("/") + "/api/v4"
        self.graphql_url = base.rstrip("/") + "/api/graphql"
        self.project_id = gitlab["project_id"]
        self.tokens = {}
        self.users = {}
        for role in ("bot", "dev", "maintainer"):
            rec = gitlab.get(role) or {}
            try:
                self.tokens[role] = Path(rec["pat_file"]).read_text(encoding="utf-8").strip()
            except (OSError, KeyError) as exc:
                raise RecordError(f"missing {role} PAT; run stack.py fixture") from exc
            self.users[role] = {"id": rec["user_id"], "username": rec["username"]}
        self.secret_values = list(self.tokens.values())
        self.created_project_access_token_ids: list[int] = []

    def call(self, role: str, method: str, path: str, params=None, body=None, url: str | None = None):
        query = urllib.parse.urlencode(params or {}, doseq=True)
        target = url or f"{self.api}/{path.lstrip('/')}" + (f"?{query}" if query else "")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(target, method=method, data=data, headers={
            "PRIVATE-TOKEN": self.tokens[role], "Content-Type": "application/json"})
        try:
            with OPENER.open(request, timeout=60) as response:
                status, headers, raw = response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as exc:
            status, headers, raw = exc.code, dict(exc.headers.items()), exc.read()
        headers = {k.lower(): v for k, v in headers.items()}
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw.decode("utf-8", "replace")[:500]
        return status, headers, parsed

    def ok(self, role: str, method: str, path: str, params=None, body=None, expect=(200, 201, 202, 204)):
        status, headers, parsed = self.call(role, method, path, params, body)
        if status not in expect:
            detail = json.dumps(scrub(parsed, self.secret_values))[:400]
            raise RecordError(f"{method} {path} as {role} -> HTTP {status}: {detail}")
        return parsed

    def record(self, role: str, path: str, params=None) -> dict:
        """GET with pagination (x-next-page), merged into one body."""
        params = dict(params or {})
        page, merged, first_headers, status = 1, None, None, None
        while True:
            status, headers, parsed = self.call(role, "GET", path, {**params, "page": page, "per_page": 100})
            first_headers = first_headers or headers
            if status != 200 or not isinstance(parsed, list):
                merged = parsed
                break
            merged = (merged or []) + parsed
            nxt = headers.get("x-next-page", "")
            if not nxt:
                break
            page = int(nxt)
        return {"request": {"method": "GET", "path": path, "query": params, "as": role},
                "status": status, "headers": {k: first_headers.get(k) for k in KEEP_HEADERS if k in first_headers},
                "body": merged}

    def graphql(self, role: str, query: str, variables: dict | None = None):
        status, _, parsed = self.call(role, "POST", "", body={"query": query, "variables": variables or {}},
                                      url=self.graphql_url)
        if status != 200 or not isinstance(parsed, dict) or parsed.get("errors"):
            raise RecordError(f"GraphQL failed HTTP {status}: {json.dumps(scrub(parsed, self.secret_values))[:400]}")
        return parsed["data"]


def revoke_created_access_tokens(gl: GitLab) -> None:
    """Best-effort all temporary project-token deletions, failing the recording if any remain."""

    failures: list[Exception] = []
    for token_id in list(gl.created_project_access_token_ids):
        try:
            gl.ok(
                "maintainer", "DELETE", f"projects/{gl.project_id}/access_tokens/{token_id}",
                expect=(200, 204, 404),
            )
        except (RecordError, OSError) as exc:
            failures.append(exc)
        else:
            gl.created_project_access_token_ids.remove(token_id)
    if failures:
        raise RecordError(
            f"failed to revoke {len(failures)} temporary project access token(s)"
        ) from failures[0]


def rails(script: str, env: dict[str, str], name: str) -> dict:
    remote = f"/tmp/bsls-record-{name}.rb"
    docker_env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
    subprocess.run(["docker", "exec", "-i", GITLAB_CONTAINER, "sh", "-c", f"umask 022; cat > {remote}"],
                   input=script, text=True, check=True, capture_output=True, env=docker_env, timeout=60)
    args = ["docker", "exec"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    try:
        proc = subprocess.run([*args, GITLAB_CONTAINER, "gitlab-rails", "runner", remote], text=True,
                              capture_output=True, env=docker_env, timeout=600)
    finally:
        subprocess.run(["docker", "exec", GITLAB_CONTAINER, "rm", "-f", remote], capture_output=True,
                       env=docker_env, timeout=60)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("BSLS_JSON=")), None)
    if proc.returncode != 0 or line is None:
        tail = TOKEN_RE.sub("glpat-[REDACTED]", (proc.stderr or proc.stdout))[-800:]
        raise RecordError(f"rails {name} failed ({proc.returncode}): {tail}")
    return json.loads(line.split("=", 1)[1])


def docker_gateway() -> str:
    proc = subprocess.run(["docker", "inspect", GITLAB_CONTAINER, "--format", "{{json .NetworkSettings.Networks}}"],
                          capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin"}, timeout=30)
    gateways = [net.get("Gateway") for net in json.loads(proc.stdout).values() if net.get("Gateway")]
    if not gateways or not ipaddress.ip_address(gateways[0]).is_private:
        raise RecordError("GitLab container has no private docker gateway")
    return gateways[0]


# ── webhook receiver ─────────────────────────────────────────────────────────


class Receiver:
    def __init__(self, host: str):
        self.secret = secrets.token_hex(24)
        self.payloads: list[dict] = []
        self.lock = threading.Lock()
        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                if self.headers.get("X-Gitlab-Token") != receiver.secret:
                    self.send_response(403)
                    self.end_headers()
                    return
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                with receiver.lock:
                    receiver.payloads.append({"event_header": self.headers.get("X-Gitlab-Event"),
                                              "received_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                              "body": body})
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                return

        self.server = http.server.ThreadingHTTPServer((host, 0), Handler)
        self.url = f"http://{host}:{self.server.server_address[1]}/hook"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False

    def kinds(self) -> set[str]:
        with self.lock:
            return {p["body"].get("object_kind") for p in self.payloads if isinstance(p["body"], dict)}


# ── scenario ─────────────────────────────────────────────────────────────────


def wait_until(what: str, predicate, timeout: float, interval: float = 2.0):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise RecordError(f"timed out waiting for {what}")
        time.sleep(interval)


def server_now(gl: GitLab) -> dt.datetime:
    _, headers, _ = gl.call("bot", "GET", "user")
    return email.utils.parsedate_to_datetime(headers["date"])


def create_objects(gl: GitLab, state: dict, stamp: str, hooks: bool) -> dict:
    pid = gl.project_id
    base = f"projects/{pid}"
    dev, maint = gl.users["dev"], gl.users["maintainer"]
    ids: dict = {"stamp": stamp}

    milestone = gl.ok("maintainer", "POST", f"{base}/milestones", body={"title": f"rec-{stamp} milestone"})
    ids["milestone_id"] = milestone["id"]
    issue = gl.ok("dev", "POST", f"{base}/issues", body={
        "title": f"Recording issue {stamp} cc @{maint['username']}",
        "description": "Recording description.\nSecond line.",
        "labels": "type::feature,status::ready,recording", "assignee_ids": [dev["id"]],
        "milestone_id": milestone["id"]})
    ids["issue_iid"] = issue["iid"]
    ids["issue_note_id"] = gl.ok("dev", "POST", f"{base}/issues/{issue['iid']}/notes", body={
        "body": f"Recording comment for @{maint['username']}\n> quoted line\n<!-- hidden -->"})["id"]
    gl.ok("maintainer", "PUT", f"{base}/issues/{issue['iid']}",
          body={"add_labels": "status::in-progress", "remove_labels": "status::ready"})
    binding = {"channel_id": state["relay"]["channel_id"], "iid": issue["iid"], "object": "issue",
               "project_id": pid, "root_event_id": "ab" * 32}
    marker = json.dumps(binding, sort_keys=True, separators=(",", ":"))
    link = f"buzz://message?channel={binding['channel_id']}&id={'ab' * 32}&thread={'ab' * 32}"
    ids["binding_note_id"] = gl.ok("bot", "POST", f"{base}/issues/{issue['iid']}/notes", body={
        "body": f"<!-- gitlab-buzz-binding:v1 {marker} -->\n🔗 Buzz Thread: {link}"})["id"]

    main_sha = gl.ok("dev", "GET", f"{base}/repository/branches/main")["commit"]["id"]
    branch = f"feature/{issue['iid']}-recording-{stamp}"
    gl.ok("dev", "POST", f"{base}/repository/branches", body={"branch": branch, "ref": "main"})
    c1 = gl.ok("dev", "POST", f"{base}/repository/commits", body={
        "branch": branch, "commit_message": f"feat: add recording app {stamp}", "actions": [
            {"action": "create", "file_path": f"src/app_{stamp}.py", "content": "print('hello')\n"},
            {"action": "create", "file_path": ".gitlab-ci.yml", "content": CI_YAML},
            {"action": "update", "file_path": "README.md",
             "content": f"# pilot\n\nbuzz-agent-setup localstack (test only)\n\nrecording {stamp}\n"}]})
    c2 = gl.ok("dev", "POST", f"{base}/repository/commits", body={
        "branch": branch, "commit_message": f"fix: second recording commit {stamp}", "actions": [
            {"action": "update", "file_path": f"src/app_{stamp}.py", "content": "print('hello, world')\n"}]})
    ids.update({"branch": branch, "commit_1": c1["id"], "commit_2": c2["id"]})
    mr = gl.ok("dev", "POST", f"{base}/merge_requests", body={
        "source_branch": branch, "target_branch": "main", "title": f"Recording MR {stamp} cc @{maint['username']}",
        "description": f"Closes #{issue['iid']}", "reviewer_ids": [maint["id"]], "labels": "type::feature",
        "milestone_id": milestone["id"]})
    ids["mr_iid"] = mr["iid"]

    draft_branch = f"feature/{issue['iid']}-draft-{stamp}"
    gl.ok("dev", "POST", f"{base}/repository/branches", body={"branch": draft_branch, "ref": "main"})
    gl.ok("dev", "POST", f"{base}/repository/commits", body={
        "branch": draft_branch, "commit_message": f"wip: draft {stamp}", "actions": [
            {"action": "create", "file_path": f"docs/draft_{stamp}.md", "content": "draft\n"}]})
    ids["draft_mr_iid"] = gl.ok("dev", "POST", f"{base}/merge_requests", body={
        "source_branch": draft_branch, "target_branch": "main", "title": f"Draft: Recording draft MR {stamp}"})["iid"]

    ids["mr_note_id"] = gl.ok("maintainer", "POST", f"{base}/merge_requests/{mr['iid']}/notes",
                              body={"body": f"Looks good @{dev['username']}"})["id"]
    gl.ok("maintainer", "POST", f"{base}/merge_requests/{mr['iid']}/approve")

    def mr_pipeline():
        pipelines = gl.ok("dev", "GET", f"{base}/merge_requests/{mr['iid']}/pipelines")
        return pipelines[0] if pipelines else None

    try:
        ids["mr_pipeline_id"] = wait_until("MR pipeline", mr_pipeline, 90)["id"]
    except RecordError:
        ids["mr_pipeline_id"] = None

    statuses = {}
    for label, sha, params in (
        # `main` is protected: a Developer gets 403 on its commit statuses, so the Maintainer posts this one.
        ("main_failed", main_sha, {"state": "failed", "name": "test:unit", "ref": "main"}),
        ("branch_success", c2["id"], {"state": "success", "name": "test:unit", "ref": branch}),
        ("mr_ref_failed", c2["id"], {"state": "failed", "name": "test:unit", "ref": f"refs/merge-requests/{mr['iid']}/head"}),
    ):
        role = "maintainer" if params["ref"] == "main" else "dev"
        status, _, body = gl.call(role, "POST", f"{base}/statuses/{sha}", body=params)
        statuses[label] = {"http": status, "pipeline_id": body.get("pipeline_id") if isinstance(body, dict) else None,
                           "ref": body.get("ref") if isinstance(body, dict) else None,
                           "message": body.get("message") if isinstance(body, dict) else None}
    ids["statuses"] = statuses

    tag = f"rec-v{stamp}"
    gl.ok("dev", "POST", f"{base}/repository/tags", body={"tag_name": tag, "ref": "main", "message": "recording tag"})
    gl.ok("dev", "POST", f"{base}/releases", body={"tag_name": tag, "name": f"Recording release {stamp} @{maint['username']}",
                                                     "description": "recording release notes"})
    ids["tag"] = tag
    deployment = gl.ok("dev", "POST", f"{base}/deployments", body={
        "environment": "rec-staging", "sha": main_sha, "ref": "main", "tag": False, "status": "running"})
    gl.ok("dev", "PUT", f"{base}/deployments/{deployment['id']}", body={"status": "success"})
    ids["deployment_id"] = deployment["id"]
    wiki = gl.ok("dev", "POST", f"{base}/wikis", body={"title": f"Recording Runbook {stamp}", "content": "v1"})
    gl.ok("dev", "PUT", f"{base}/wikis/{urllib.parse.quote(wiki['slug'], safe='')}", body={"content": "v2"})
    ids["wiki_slug"] = wiki["slug"]
    gl.ok("maintainer", "PUT", f"{base}/milestones/{milestone['id']}", body={"state_event": "close"})
    flag = f"rec_flag_{stamp}"
    gl.ok("dev", "POST", f"{base}/feature_flags", body={"name": flag, "version": "new_version_flag", "active": True,
                                                         "description": "recording flag"})
    gl.ok("dev", "PUT", f"{base}/feature_flags/{flag}", body={"active": False})
    ids["feature_flag"] = flag
    token_name = f"rec-expiring-{stamp}"
    expires = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    created = gl.ok("maintainer", "POST", f"{base}/access_tokens", body={
        "name": token_name, "scopes": ["read_api"], "access_level": 20, "expires_at": expires})
    gl.created_project_access_token_ids.append(created["id"])
    if isinstance(created, dict) and created.get("token"):
        gl.secret_values.append(created["token"])  # never stored; scrubbed if echoed anywhere
    ids.update({"access_token_name": token_name, "access_token_id": created["id"], "access_token_expires_at": expires})
    ids["commit_note"] = gl.ok("dev", "POST", f"{base}/repository/commits/{c1['id']}/comments",
                               body={"note": "Recording commit comment"}).get("note")
    temp = f"rec-temp-{stamp}"
    gl.ok("dev", "POST", f"{base}/repository/branches", body={"branch": temp, "ref": "main"})
    gl.ok("dev", "DELETE", f"{base}/repository/branches/{urllib.parse.quote(temp, safe='')}")
    ids["temp_branch"] = temp

    task_type = gl.graphql("maintainer", """query($p: ID!) { project(fullPath: $p) {
        workItemTypes(name: TASK) { nodes { id name } } } }""", {"p": state["gitlab"]["project_path"]})
    task_type_id = task_type["project"]["workItemTypes"]["nodes"][0]["id"]
    created_task = gl.graphql("maintainer", """mutation($i: WorkItemCreateInput!) { workItemCreate(input: $i) {
        workItem { id iid title workItemType { name } } errors } }""", {"i": {
        "namespacePath": state["gitlab"]["project_path"], "title": f"Recording task {stamp}",
        "workItemTypeId": task_type_id}})["workItemCreate"]["workItem"]
    gl.graphql("maintainer", """mutation($i: WorkItemUpdateInput!) { workItemUpdate(input: $i) { errors } }""",
               {"i": {"id": created_task["id"], "title": f"Recording task {stamp} renamed"}})
    ids["task_iid"] = int(created_task["iid"])

    drop = rails(RUBY_DROP_AND_TOKEN_HOOK, {
        "BSLS_PROJECT_ID": str(pid), "BSLS_DROP_PIPELINES": str(ids["mr_pipeline_id"] or ""),
        "BSLS_TOKEN_NAME": token_name if hooks else ""}, "drop")
    ids["rails_drop"] = drop
    return ids


def record_api(gl: GitLab, ids: dict, since: str, phase: str) -> dict[str, dict]:
    pid, base = gl.project_id, f"projects/{gl.project_id}"
    after = (dt.datetime.fromisoformat(since.replace("Z", "+00:00")) - dt.timedelta(days=1)).date().isoformat()
    listing = {"state": "all", "order_by": "created_at", "sort": "asc", "updated_after": since}
    mr, issue = ids["mr_iid"], ids["issue_iid"]
    out: dict[str, dict] = {}
    if phase == "open":
        out["merge_request_opened"] = gl.record("bot", f"{base}/merge_requests/{mr}")
        out["merge_request_draft"] = gl.record("bot", f"{base}/merge_requests/{ids['draft_mr_iid']}")
        out["merge_request_approvals"] = gl.record("bot", f"{base}/merge_requests/{mr}/approvals")
        out["merge_request_closes_issues"] = gl.record("bot", f"{base}/merge_requests/{mr}/closes_issues")
        out["merge_request_diffs"] = gl.record("bot", f"{base}/merge_requests/{mr}/diffs")
        return out
    out["user"] = gl.record("bot", "user")
    out["project"] = gl.record("bot", base)
    out["members_all"] = gl.record("bot", f"{base}/members/all")
    out["issues"] = gl.record("bot", f"{base}/issues", listing)
    out["issue_notes"] = gl.record("bot", f"{base}/issues/{issue}/notes", {"sort": "asc"})
    out["merge_requests"] = gl.record("bot", f"{base}/merge_requests", listing)
    out["merge_request_merged"] = gl.record("bot", f"{base}/merge_requests/{mr}")
    out["merge_request_notes"] = gl.record("bot", f"{base}/merge_requests/{mr}/notes", {"sort": "asc"})
    out["events"] = gl.record("bot", f"{base}/events", {"after": after, "sort": "asc"})
    out["pipelines"] = gl.record("bot", f"{base}/pipelines",
                                 {"updated_after": since, "order_by": "updated_at", "sort": "asc"})
    for pipeline in out["pipelines"]["body"] or []:
        name = "pipeline_jobs_failed_" + ("mr" if pipeline["id"] == ids.get("mr_pipeline_id") else str(pipeline["ref"]).replace("/", "_"))
        if name not in out:
            out[name] = gl.record("bot", f"{base}/pipelines/{pipeline['id']}/jobs", {"scope[]": "failed"})
    out["deployments"] = gl.record("bot", f"{base}/deployments",
                                   {"updated_after": since, "order_by": "updated_at", "sort": "asc"})
    out["releases"] = gl.record("bot", f"{base}/releases", {"order_by": "created_at", "sort": "desc"})
    out["feature_flags_as_bot"] = gl.record("bot", f"{base}/feature_flags")
    out["feature_flags"] = gl.record("maintainer", f"{base}/feature_flags")
    out["access_tokens_as_bot"] = gl.record("bot", f"{base}/access_tokens")
    out["access_tokens"] = gl.record("maintainer", f"{base}/access_tokens")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(HERE), help="fixture directory (default: this directory)")
    parser.add_argument("--webhooks", action="store_true", help="also record project-webhook payloads")
    parser.add_argument("--allow-local-original", choices=("true", "false"),
                        help="value to restore for allow_local_requests_from_web_hooks_and_services "
                             "(default: the value observed before recording)")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    result: dict = {"ok": False}
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("status") != "up":
            raise RecordError("localstack is not up")
        gl = GitLab(state)
        start = server_now(gl) - dt.timedelta(seconds=5)
        since = start.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        stamp = start.strftime("%m%d%H%M%S")
        version = gl.ok("bot", "GET", "version")
        hook_id, receiver, restore = None, None, None
        recordings: dict[str, dict] = {}
        try:
            if args.webhooks:
                restore = rails(RUBY_ALLOW_LOCAL, {"BSLS_ALLOW_LOCAL": "true"}, "allow-local")
                receiver = Receiver(docker_gateway()).__enter__()
                hook = gl.ok("maintainer", "POST", f"projects/{gl.project_id}/hooks", body={
                    "url": receiver.url, "token": receiver.secret, "enable_ssl_verification": False,
                    **{flag: True for flag in (
                        "push_events", "tag_push_events", "issues_events", "confidential_issues_events",
                        "note_events", "confidential_note_events", "merge_requests_events", "job_events",
                        "pipeline_events", "wiki_page_events", "deployment_events", "releases_events",
                        "feature_flag_events", "resource_access_token_events", "milestone_events")}})
                hook_id = hook["id"]
                result["hook_flags"] = sorted(k for k, v in hook.items() if k.endswith("_events") and v is True)
            ids = create_objects(gl, state, stamp, args.webhooks)
            recordings.update(record_api(gl, ids, since, "open"))
            gl.ok("maintainer", "PUT", f"projects/{gl.project_id}/merge_requests/{ids['mr_iid']}/merge")
            wait_until("MR merged", lambda: gl.ok("bot", "GET", f"projects/{gl.project_id}/merge_requests/{ids['mr_iid']}")
                       .get("state") == "merged", 60)
            gl.ok("maintainer", "PUT", f"projects/{gl.project_id}/milestones/{ids['milestone_id']}",
                  body={"state_event": "activate"})
            recordings.update(record_api(gl, ids, since, "final"))
            if receiver:
                try:
                    wait_until("webhook kinds", lambda: EXPECTED_WEBHOOK_KINDS <= receiver.kinds(), 180)
                except RecordError:
                    log(f"missing webhook kinds: {sorted(EXPECTED_WEBHOOK_KINDS - receiver.kinds())}")
                time.sleep(5)  # late deliveries (pipeline after drop, merge side effects)
        finally:
            try:
                if hook_id:
                    gl.call("maintainer", "DELETE", f"projects/{gl.project_id}/hooks/{hook_id}")
                if receiver:
                    receiver.__exit__(None, None, None)
                if restore is not None:
                    original = args.allow_local_original or ("true" if restore.get("previous") else "false")
                    if original == "false":
                        result["allow_local_restored"] = rails(
                            RUBY_ALLOW_LOCAL, {"BSLS_ALLOW_LOCAL": "false"}, "restore"
                        )
            finally:
                revoke_created_access_tokens(gl)

        api_dir = out_dir / "api"
        api_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for name, rec in sorted(recordings.items()):
            path = api_dir / f"{name}.json"
            path.write_text(json.dumps(scrub(rec, gl.secret_values), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                            encoding="utf-8")
            written.append(str(path.relative_to(out_dir)))
        if receiver:
            hook_dir = out_dir / "webhooks"
            hook_dir.mkdir(parents=True, exist_ok=True)
            for index, payload in enumerate(receiver.payloads, 1):
                body = payload["body"] if isinstance(payload["body"], dict) else {}
                attrs = body.get("object_attributes") if isinstance(body.get("object_attributes"), dict) else {}
                action = attrs.get("action") or body.get("action") or attrs.get("status") or body.get("status") or body.get("event_name") or "x"
                name = f"{index:02d}-{body.get('object_kind', 'unknown')}-{re.sub(r'[^a-z0-9_]+', '_', str(action).lower())}.json"
                (hook_dir / name).write_text(json.dumps(scrub(payload, gl.secret_values), indent=2, sort_keys=True,
                                                        ensure_ascii=False) + "\n", encoding="utf-8")
                written.append(f"webhooks/{name}")
            result["webhook_kinds"] = sorted(k for k in receiver.kinds() if k)
        meta = {"gitlab_version": version.get("version"), "gitlab_revision": version.get("revision"),
                "recorded_at": since, "project_id": gl.project_id, "objects": ids,
                "users": {role: user for role, user in gl.users.items()},
                "scrubbed": ["tokens", "e-mail addresses", "avatar URLs", f"LAN host -> {SCRUBBED_HOST}"]}
        (out_dir / "_meta.json").write_text(json.dumps(scrub(meta, gl.secret_values), indent=2, sort_keys=True,
                                                       ensure_ascii=False) + "\n", encoding="utf-8")
        result.update({"ok": True, "files": len(written), "objects": scrub(ids, gl.secret_values)})
        code = 0
    except (RecordError, OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        result["error"] = TOKEN_RE.sub("glpat-[REDACTED]", f"{type(exc).__name__}: {exc}")
        code = 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
