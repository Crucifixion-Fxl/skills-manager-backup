#!/usr/bin/env python3
"""Route GitLab issue lifecycle events into one durable Buzz thread per issue.

Run one process instance per business Channel.  The instance owns exactly one
GitLab project, one Buzz Channel, one deterministic adapter identity, and one
route configuration.  Secrets come from environment variables; the JSON
configuration contains only environment-variable names and public keys.

The adapter deliberately does not make semantic decisions.  It creates or
recovers the Issue -> Thread binding, posts lifecycle facts to that Thread, and
wakes Desk only when the primary route target changes.  Desk validates the
Issue and explicitly mentions the target role Agent in the same Thread.

Usage:
  issue_thread_router.py --config issue-thread-router.json
  issue_thread_router.py --config issue-thread-router.json --dry-run
  issue_thread_router.py --config issue-thread-router.json \
      --resolve-route feature ready opened
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


BINDING_PREFIX = "buzz-thread-binding:v1"
ROOT_MARKER_PREFIX = "issue-route:v1"
EVENT_MARKER_PREFIX = "issue-route-event:v1"
EVENT_ACTIONS = {"opened", "updated", "closed", "reopened"}


class RouterError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RouterError(f"{path}: top-level JSON value must be an object")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def validate_config(config: dict[str, Any]) -> None:
    required = {
        "business",
        "gitlab",
        "buzz",
        "agents",
        "routes",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise RouterError(f"config missing keys: {', '.join(missing)}")

    gitlab = config["gitlab"]
    buzz = config["buzz"]
    for key in ("base_url", "project_id", "project_web_url", "token_env"):
        if key not in gitlab:
            raise RouterError(f"config.gitlab.{key} is required")
    for key in ("channel_id", "desk_agent"):
        if key not in buzz:
            raise RouterError(f"config.buzz.{key} is required")

    agents = config["agents"]
    desk_key = buzz["desk_agent"]
    if desk_key not in agents:
        raise RouterError(f"desk agent {desk_key!r} is absent from config.agents")
    for key, agent in agents.items():
        if not agent.get("name") or not re.fullmatch(r"[0-9a-fA-F]{64}", agent.get("pubkey", "")):
            raise RouterError(f"agent {key!r} needs name and 64-char hex pubkey")

    for index, rule in enumerate(config["routes"]):
        if not isinstance(rule, dict):
            raise RouterError(f"routes[{index}] must be an object")
        if rule.get("target") not in agents:
            raise RouterError(f"routes[{index}].target is not present in config.agents")
        if not rule.get("types") or not rule.get("statuses"):
            raise RouterError(f"routes[{index}] needs non-empty types and statuses")


def one_label(labels: list[str], prefix: str) -> tuple[str | None, bool]:
    values = [label[len(prefix):] for label in labels if label.startswith(prefix)]
    return (values[0] if len(values) == 1 else None, len(values) == 1)


def issue_snapshot(issue: dict[str, Any]) -> dict[str, Any]:
    labels = [str(value) for value in issue.get("labels") or []]
    issue_type, type_valid = one_label(labels, "type::")
    status, status_valid = one_label(labels, "status::")
    return {
        "state": issue.get("state") or "unknown",
        "type": issue_type,
        "status": status,
        "labels_valid": type_valid and status_valid,
        "updated_at": issue.get("updated_at"),
    }


def resolve_target(config: dict[str, Any], snapshot: dict[str, Any]) -> str:
    desk = config["buzz"]["desk_agent"]
    if snapshot["state"] == "closed" or not snapshot["labels_valid"]:
        return desk
    issue_type = snapshot["type"]
    status = snapshot["status"]
    for rule in config["routes"]:
        if issue_type in rule["types"] and status in rule["statuses"]:
            return rule["target"]
    return desk


class GitLab:
    def __init__(self, config: dict[str, Any]):
        gitlab = config["gitlab"]
        token_env = gitlab["token_env"]
        token = os.environ.get(token_env)
        if not token:
            raise RouterError(f"missing GitLab token environment variable {token_env}")
        self.api = gitlab["base_url"].rstrip("/") + "/api/v4"
        self.project_id = str(gitlab["project_id"])
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, str] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.api}/{path.lstrip('/')}" + (f"?{query}" if query else "")
        payload = urllib.parse.urlencode(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            method=method,
            data=payload,
            headers={"PRIVATE-TOKEN": self.token, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                headers = {key.lower(): value for key, value in response.headers.items()}
                raw = response.read()
                return (json.loads(raw) if raw else None, headers)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RouterError(f"GitLab {method} {path} failed: HTTP {exc.code}: {detail}") from exc

    def paged(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        page = 1
        result: list[dict[str, Any]] = []
        while True:
            page_params = {**params, "page": str(page), "per_page": "100"}
            values, headers = self.request("GET", path, params=page_params)
            if not isinstance(values, list):
                raise RouterError(f"GitLab {path} did not return a list")
            result.extend(values)
            next_page = headers.get("x-next-page", "")
            if not next_page:
                return result
            page = int(next_page)

    def events(self, cursor: str) -> list[dict[str, Any]]:
        after = (parse_time(cursor) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        events = self.paged(
            f"projects/{self.project_id}/events",
            {"after": after, "sort": "asc"},
        )
        return sorted(events, key=lambda event: (event.get("created_at", ""), str(event.get("id", ""))))

    def issue(self, iid: int | str) -> dict[str, Any]:
        value, _ = self.request("GET", f"projects/{self.project_id}/issues/{iid}")
        if not isinstance(value, dict):
            raise RouterError(f"GitLab issue {iid} did not return an object")
        return value

    def open_issues(self) -> list[dict[str, Any]]:
        return self.paged(
            f"projects/{self.project_id}/issues",
            {"state": "opened", "order_by": "created_at", "sort": "asc"},
        )

    def notes(self, iid: int | str) -> list[dict[str, Any]]:
        return self.paged(f"projects/{self.project_id}/issues/{iid}/notes", {"sort": "desc"})

    def add_note(self, iid: int | str, body: str) -> None:
        self.request("POST", f"projects/{self.project_id}/issues/{iid}/notes", body={"body": body})


class Buzz:
    def __init__(self, config: dict[str, Any], dry_run: bool):
        self.channel = config["buzz"]["channel_id"]
        self.dry_run = dry_run

    def command(self, args: list[str], content: str | None = None) -> Any:
        if self.dry_run:
            print("DRY buzz", " ".join(args), (content or "").replace("\n", " | ")[:240])
            return {"accepted": True, "event_id": "DRY-RUN-EVENT-ID"}
        result = subprocess.run(
            ["buzz", *args],
            input=content,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:500]
            raise RouterError(f"buzz {' '.join(args[:2])} failed ({result.returncode}): {detail}")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RouterError(f"buzz returned non-JSON output: {result.stdout[:500]}") from exc
        return value

    def send(
        self,
        content: str,
        *,
        root_event_id: str | None = None,
        mention_pubkey: str | None = None,
    ) -> str:
        args = ["messages", "send", "--channel", self.channel, "--content", "-"]
        if root_event_id:
            args += ["--reply-to", root_event_id]
        if mention_pubkey:
            args += ["--mention", mention_pubkey]
        value = self.command(args, content)
        if not value.get("accepted") or not value.get("event_id"):
            raise RouterError(f"Buzz rejected message: {value}")
        return str(value["event_id"])

    def recover_root(self, marker: str) -> str | None:
        if self.dry_run:
            return None
        value = self.command(["messages", "search", "--query", marker, "--limit", "20"])
        if not isinstance(value, list):
            return None
        for event in value:
            tags = event.get("tags") or []
            in_channel = any(len(tag) >= 2 and tag[0] == "h" and tag[1] == self.channel for tag in tags)
            is_reply = any(tag and tag[0] == "e" for tag in tags)
            if in_channel and not is_reply and marker in (event.get("content") or ""):
                return str(event["id"])
        return None

    def contains_message(self, marker: str, root_event_id: str) -> bool:
        if self.dry_run:
            return False
        value = self.command(["messages", "search", "--query", marker, "--limit", "20"])
        if not isinstance(value, list):
            return False
        for event in value:
            tags = event.get("tags") or []
            in_channel = any(len(tag) >= 2 and tag[0] == "h" and tag[1] == self.channel for tag in tags)
            replies_to_root = any(
                len(tag) >= 4
                and tag[0] == "e"
                and tag[1] == root_event_id
                and tag[3] == "reply"
                for tag in tags
            )
            if in_channel and replies_to_root and marker in (event.get("content") or ""):
                return True
        return False


def binding_marker(channel_id: str, root_event_id: str) -> str:
    value = json.dumps(
        {"channel_id": channel_id, "root_event_id": root_event_id},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"<!-- {BINDING_PREFIX} {value} -->"


def parse_binding(notes: list[dict[str, Any]], expected_channel: str) -> str | None:
    pattern = re.compile(rf"<!--\s*{re.escape(BINDING_PREFIX)}\s+(\{{.*?\}})\s*-->")
    for note in notes:
        match = pattern.search(note.get("body") or "")
        if not match:
            continue
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if value.get("channel_id") != expected_channel:
            raise RouterError(
                f"Issue already binds to Channel {value.get('channel_id')}, expected {expected_channel}"
            )
        root = value.get("root_event_id")
        if isinstance(root, str) and root:
            return root
    return None


def route_changed(previous: dict[str, Any] | None, current: dict[str, Any], target: str) -> bool:
    if previous is None:
        return True
    return previous.get("target") != target


def snapshot_changed(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if previous is None:
        return True
    keys = ("state", "type", "status", "labels_valid")
    return any(previous.get(key) != current.get(key) for key in keys)


class Router:
    def __init__(
        self,
        config: dict[str, Any],
        state_path: Path,
        *,
        dry_run: bool,
    ):
        validate_config(config)
        self.config = config
        self.state_path = state_path
        self.dry_run = dry_run
        self.gitlab = GitLab(config)
        self.buzz = Buzz(config, dry_run)
        try:
            self.state = load_json(state_path)
        except FileNotFoundError:
            self.state = {"cursor": None, "seen_event_ids": [], "issues": {}}
        self.state.setdefault("seen_event_ids", [])
        self.state.setdefault("issues", {})

    @property
    def project_id(self) -> str:
        return str(self.config["gitlab"]["project_id"])

    def save(self) -> None:
        if not self.dry_run:
            atomic_write_json(self.state_path, self.state)

    def root_marker(self, iid: int | str) -> str:
        return f"[{ROOT_MARKER_PREFIX}:{self.project_id}:{iid}]"

    def ensure_binding(self, issue: dict[str, Any], current: dict[str, Any]) -> tuple[str, bool]:
        iid = str(issue["iid"])
        issue_state = self.state["issues"].setdefault(iid, {})
        root = issue_state.get("root_event_id")
        if root:
            if not issue_state.get("binding_note_written"):
                note_root = parse_binding(self.gitlab.notes(iid), self.buzz.channel)
                if note_root and note_root != root:
                    raise RouterError(f"Issue #{iid} has conflicting root_event_id values")
                if not note_root and not self.dry_run:
                    self.write_binding_note(issue, str(root))
                issue_state["binding_note_written"] = True
                self.save()
            return str(root), False

        root = parse_binding(self.gitlab.notes(iid), self.buzz.channel)
        if root:
            issue_state["root_event_id"] = root
            issue_state["binding_note_written"] = True
            self.save()
            return root, False

        marker = self.root_marker(iid)
        root = self.buzz.recover_root(marker)
        if root:
            issue_state["root_event_id"] = root
            self.save()
            if not self.dry_run:
                self.write_binding_note(issue, root)
            issue_state["binding_note_written"] = True
            self.save()
            return root, False

        desk = self.config["agents"][self.config["buzz"]["desk_agent"]]
        labels = ", ".join(issue.get("labels") or []) or "(none)"
        content = (
            f"{marker}\n"
            f"📌 Issue #{iid} · {issue.get('title', '')}\n"
            f"{self.config['gitlab']['project_web_url'].rstrip('/')}/-/issues/{iid}\n"
            f"state={current['state']} · type={current['type'] or '(missing)'} · "
            f"status={current['status'] or '(missing)'}\n"
            f"labels: {labels}\n\n"
            f"@{desk['name']} 新 Issue：请读取原始内容，校验/补齐单值 type:: 与 status::，"
            "再按本业务 Channel 的路由规则在本 Thread 指派下一位角色 Agent。"
        )
        root = self.buzz.send(content, mention_pubkey=desk["pubkey"])
        issue_state["root_event_id"] = root
        self.save()  # Persist before the GitLab note to narrow the crash duplicate window.
        if not self.dry_run:
            self.write_binding_note(issue, root)
        issue_state["binding_note_written"] = True
        self.save()
        return root, True

    def write_binding_note(self, issue: dict[str, Any], root: str) -> None:
        channel = self.buzz.channel
        marker = binding_marker(channel, root)
        link = f"buzz://message?channel={channel}&id={root}&thread={root}"
        self.gitlab.add_note(
            issue["iid"],
            f"{marker}\n🔗 Buzz discussion Thread: {link}\n"
            "该 comment 是 Issue→Thread 的机器绑定；请勿手工复制到其他 Issue。",
        )

    def post_transition(
        self,
        event: dict[str, Any],
        issue: dict[str, Any],
        previous: dict[str, Any] | None,
        current: dict[str, Any],
        target_key: str,
        root: str,
        *,
        root_created: bool,
    ) -> None:
        if root_created:
            return
        target = self.config["agents"][target_key]
        desk_key = self.config["buzz"]["desk_agent"]
        desk = self.config["agents"][desk_key]
        changed = route_changed(previous, current, target_key)
        event_marker = f"[{EVENT_MARKER_PREFIX}:{self.project_id}:{event.get('id')}]"
        if self.buzz.contains_message(event_marker, root):
            return
        before = (
            "none"
            if previous is None
            else f"{previous.get('state')}/{previous.get('type')}/{previous.get('status')}"
        )
        after = f"{current['state']}/{current['type']}/{current['status']}"
        action = event.get("action_name") or "updated"
        if changed:
            if target_key == desk_key:
                instruction = (
                    f"@{desk['name']} Issue 已关闭或路由无效：请复核并在本 Thread 写结束/人工处理摘要；"
                    "不要 @ 自己，也不要自动路由 executor。"
                )
            else:
                instruction = (
                    f"@{desk['name']} 路由目标已变化。建议目标：{target['name']} "
                    f"(pubkey={target['pubkey']})。请重新读取 Issue，确认 type/status 与上下文一致；"
                    "确认后使用显式 pubkey 在本 Thread @ 目标角色 Agent。"
                    "executor 永远不能被自动路由。"
                )
            mention = desk["pubkey"]
        else:
            instruction = (
                f"主责仍是 {target['name']}；仅记录状态，不重复 @，避免 Agent 自唤醒循环。"
            )
            mention = None
        content = (
            f"{event_marker}\n"
            f"🔄 Issue #{issue['iid']} {action}: {before} → {after}\n"
            f"{self.config['gitlab']['project_web_url'].rstrip('/')}/-/issues/{issue['iid']}\n"
            f"{instruction}"
        )
        self.buzz.send(content, root_event_id=root, mention_pubkey=mention)

    def process_event(self, event: dict[str, Any]) -> None:
        iid = event.get("target_iid")
        if not iid:
            raise RouterError(f"Issue event {event.get('id')} has no target_iid")
        issue = self.gitlab.issue(iid)
        current = issue_snapshot(issue)
        issue_state = self.state["issues"].setdefault(str(iid), {})
        previous = issue_state.get("snapshot")
        target = resolve_target(self.config, current)

        root, created = self.ensure_binding(issue, current)
        if snapshot_changed(previous, current) or event.get("action_name") in {"closed", "reopened"}:
            self.post_transition(event, issue, previous, current, target, root, root_created=created)
        issue_state["snapshot"] = {**current, "target": target}
        self.save()

    def run(self, *, bootstrap_existing: bool) -> None:
        if not self.state.get("cursor"):
            if bootstrap_existing:
                issues = self.gitlab.open_issues()
                print(f"bootstrap: {len(issues)} currently open Issues")
                for issue in issues:
                    self.process_event(
                        {
                            "id": f"bootstrap-{issue['iid']}",
                            "action_name": "opened",
                            "target_type": "Issue",
                            "target_iid": issue["iid"],
                            "created_at": issue.get("created_at") or utc_now(),
                        }
                    )
                self.state["cursor"] = utc_now()
                self.save()
                print(f"bootstrap done; cursor={self.state['cursor']}")
                return
            self.state["cursor"] = utc_now()
            self.save()
            print(f"initialized cursor={self.state['cursor']}; existing Issues were not routed")
            return

        cursor = str(self.state["cursor"])
        seen_list = [str(value) for value in self.state.get("seen_event_ids", [])]
        seen = set(seen_list)
        events = self.gitlab.events(cursor)
        processed = 0
        for event in events:
            event_id = str(event.get("id", ""))
            created_at = event.get("created_at") or cursor
            if event_id in seen or created_at < cursor:
                continue
            kind = event.get("target_type")
            action = event.get("action_name")
            relevant = kind in {"Issue", "WorkItem"} and action in EVENT_ACTIONS
            try:
                if relevant:
                    self.process_event(event)
                if event_id not in seen:
                    seen.add(event_id)
                    seen_list.append(event_id)
                self.state["seen_event_ids"] = seen_list[-1000:]
                self.state["cursor"] = max(cursor, created_at)
                self.save()
                cursor = self.state["cursor"]
                processed += int(relevant)
            except Exception:
                # Never advance past a failed event.  Next run replays it.
                raise
        print(f"done: routed {processed} Issue events; cursor={self.state['cursor']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bootstrap-existing", action="store_true")
    parser.add_argument(
        "--resolve-route",
        nargs=3,
        metavar=("TYPE", "STATUS", "STATE"),
        help="print target Agent for a route tuple without contacting GitLab/Buzz",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_json(args.config)
    validate_config(config)
    if args.resolve_route:
        issue_type, status, state = args.resolve_route
        snapshot = {
            "type": None if issue_type == "-" else issue_type,
            "status": None if status == "-" else status,
            "state": state,
            "labels_valid": issue_type != "-" and status != "-",
        }
        key = resolve_target(config, snapshot)
        print(json.dumps({"target": key, **config["agents"][key]}, ensure_ascii=False))
        return 0

    state_path = args.state or Path(
        os.path.expanduser(f"~/.config/buzz/agents/.issue-thread-router-{config['business']}.json")
    )
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another router instance is running; skipped")
            return 0
        Router(config, state_path, dry_run=args.dry_run).run(
            bootstrap_existing=args.bootstrap_existing
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RouterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
