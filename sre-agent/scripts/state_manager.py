#!/usr/bin/env python3
"""
sre-agent 状态管理器。
管理 .sre-agent/ 目录下的告警状态、pending batch、调查结果、审批状态。
"""

import json
import os
import glob as glob_mod
from datetime import datetime, timezone, timedelta

try:
    import yaml
except ImportError:
    yaml = None  # yaml 仅用于 CG result，无 yaml 时 fallback 到 json


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


_EMPTY_STATE = {
    "last_poll_at": None,
    "processed_incident_ids": [],
    "active_correlation_groups": {},
    "completed_correlation_groups": {},
    "cg_counter": 1,
}


class StateManager:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        self._state_file = os.path.join(state_dir, "alert_state.json")
        self._investigations_dir = os.path.join(state_dir, "investigations")
        self._executions_dir = os.path.join(state_dir, "executions")
        self._approvals_dir = os.path.join(state_dir, "approvals")
        self._knowledge_dir = os.path.join(state_dir, "knowledge")

        for d in [self._investigations_dir, self._executions_dir, self._approvals_dir, self._knowledge_dir]:
            os.makedirs(d, exist_ok=True)

        if not os.path.isfile(self._state_file):
            self._save_state(_EMPTY_STATE.copy())

    # ─── State file I/O ───

    def reset_state(self):
        """重置状态为空（debug 模式每次运行前调用）。"""
        self._save_state(_EMPTY_STATE.copy())

    def load_state(self):
        with open(self._state_file, "r") as f:
            return json.load(f)

    def _save_state(self, state):
        with open(self._state_file, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def update_active_cg(self, cg_id, updates):
        """Update fields on an active CG. Returns True if updated, False if cg_id not found."""
        state = self.load_state()
        if cg_id in state["active_correlation_groups"]:
            state["active_correlation_groups"][cg_id].update(updates)
            self._save_state(state)
            return True
        return False

    def set_solutions_status(self, cg_id, status_dict):
        """Set solutions_status on a completed CG. Returns True if updated, False if cg_id not found."""
        state = self.load_state()
        if cg_id in state["completed_correlation_groups"]:
            state["completed_correlation_groups"][cg_id]["solutions_status"] = status_dict
            self._save_state(state)
            return True
        return False

    def trim_processed_ids(self, max_ids=1000):
        """Trim processed_incident_ids to keep only the most recent max_ids."""
        state = self.load_state()
        if len(state["processed_incident_ids"]) > max_ids:
            state["processed_incident_ids"] = state["processed_incident_ids"][-max_ids:]
            self._save_state(state)

    # ─── Correlation Groups ───

    def create_cg(self, incidents, service, environment,
                  is_recurrence=False, recurrence_of=None, fault_entity=None, service_entity=None):
        state = self.load_state()
        cg_id = f"CG-{state['cg_counter']}"
        state["active_correlation_groups"][cg_id] = {
            "created_at": _now_iso(),
            "incidents": list(incidents),
            "service": service,
            "environment": environment,
            "status": "dispatch_pending",
            "investigation_agent_id": None,
            "phase1_sent": False,
            "investigating_since": None,
            "is_recurrence": is_recurrence,
            "recurrence_of": recurrence_of,
            "fault_entity": fault_entity,
            "service_entity": service_entity,
            "review_dispatched": False,
            "revision_count": 0,
            "review_retry_count": 0,
            "investigation_retry_count": 0,
        }
        state["cg_counter"] += 1
        self._save_state(state)
        return cg_id

    def add_incidents_to_cg(self, cg_id, incident_ids):
        state = self.load_state()
        cg = state["active_correlation_groups"][cg_id]
        cg["incidents"].extend(incident_ids)
        self._save_state(state)

    def complete_cg(self, cg_id):
        state = self.load_state()
        cg = state["active_correlation_groups"].pop(cg_id)
        state["completed_correlation_groups"][cg_id] = {
            "completed_at": _now_iso(),
            "result_path": f"investigations/{cg_id}/report.yaml",
            "incidents": cg["incidents"],
            "service": cg["service"],
            "environment": cg["environment"],
            "solutions_status": {},
        }
        self._save_state(state)

    def confirm_investigating(self, cg_id):
        """Subagent confirms startup. Only allows dispatch_pending → investigating."""
        state = self.load_state()
        cg = state["active_correlation_groups"].get(cg_id)
        if not cg:
            raise ValueError(f"{cg_id} not found in active CGs")
        if cg["status"] != "dispatch_pending":
            raise ValueError(
                f"{cg_id} status is '{cg['status']}', expected 'dispatch_pending'"
            )
        cg["status"] = "investigating"
        cg["investigating_since"] = _now_iso()
        self._save_state(state)

    def get_active_cgs(self):
        return self.load_state()["active_correlation_groups"]

    def get_completed_cgs(self):
        return self.load_state()["completed_correlation_groups"]

    # ─── Processed Incidents ───

    def mark_processed(self, incident_ids):
        state = self.load_state()
        state["processed_incident_ids"].extend(incident_ids)
        self._save_state(state)

    def is_processed(self, incident_id):
        return incident_id in self.load_state()["processed_incident_ids"]

    def filter_new_incidents(self, incident_ids):
        processed = set(self.load_state()["processed_incident_ids"])
        return [iid for iid in incident_ids if iid not in processed]

    def update_last_poll(self, timestamp=None):
        state = self.load_state()
        state["last_poll_at"] = timestamp or _now_iso()
        self._save_state(state)

    # ─── Pending Batches ───

    def save_pending_batch(self, cg_id, batch_num, incidents_data):
        pending_alerts_dir = os.path.join(self._investigations_dir, cg_id, "pending-alerts")
        os.makedirs(pending_alerts_dir, exist_ok=True)
        path = os.path.join(pending_alerts_dir, f"batch-{batch_num}.json")
        with open(path, "w") as f:
            json.dump(incidents_data, f, indent=2, ensure_ascii=False)

    def load_pending_batches(self, cg_id):
        pattern = os.path.join(self._investigations_dir, cg_id, "pending-alerts", "batch-*.json")
        batches = []
        for path in sorted(glob_mod.glob(pattern)):
            with open(path, "r") as f:
                batches.append(json.load(f))
        return batches

    def clear_pending(self, cg_id):
        pattern = os.path.join(self._investigations_dir, cg_id, "pending-alerts", "batch-*.json")
        for path in glob_mod.glob(pattern):
            os.remove(path)

    # ─── CG Results ───

    def save_cg_result(self, cg_id, result):
        cg_dir = os.path.join(self._investigations_dir, cg_id)
        os.makedirs(cg_dir, exist_ok=True)
        path = os.path.join(cg_dir, "report.yaml")
        with open(path, "w") as f:
            if yaml:
                yaml.dump(result, f, default_flow_style=False, allow_unicode=True)
            else:
                json.dump(result, f, indent=2, ensure_ascii=False)

    def mark_review_dispatched(self, cg_id):
        """Mark that a review subagent has been dispatched for this CG."""
        state = self.load_state()
        cg = state["active_correlation_groups"].get(cg_id)
        if not cg:
            raise ValueError(f"{cg_id} not found in active CGs")
        cg["review_dispatched"] = True
        self._save_state(state)

    def reset_for_revision(self, cg_id):
        """Reset CG for re-investigation after review rejection."""
        state = self.load_state()
        cg = state["active_correlation_groups"].get(cg_id)
        if not cg:
            raise ValueError(f"{cg_id} not found in active CGs")
        cg["status"] = "dispatch_pending"
        cg["review_dispatched"] = False
        cg["revision_count"] = cg.get("revision_count", 0) + 1
        cg["investigating_since"] = None
        self._save_state(state)

    def load_review_result(self, cg_id):
        """Load review.yaml for a CG. Returns dict or None."""
        cg_dir = os.path.join(self._investigations_dir, cg_id)
        yaml_path = os.path.join(cg_dir, "review.yaml")
        if not os.path.isfile(yaml_path):
            return None
        with open(yaml_path, "r") as f:
            if yaml:
                return yaml.safe_load(f)
            return json.load(f)

    def save_review_result(self, cg_id, result):
        """Save review.yaml for a CG."""
        cg_dir = os.path.join(self._investigations_dir, cg_id)
        os.makedirs(cg_dir, exist_ok=True)
        path = os.path.join(cg_dir, "review.yaml")
        with open(path, "w") as f:
            if yaml:
                yaml.dump(result, f, default_flow_style=False, allow_unicode=True)
            else:
                json.dump(result, f, indent=2, ensure_ascii=False)

    def delete_review_result(self, cg_id):
        """Delete review.yaml (before re-investigation)."""
        cg_dir = os.path.join(self._investigations_dir, cg_id)
        path = os.path.join(cg_dir, "review.yaml")
        if os.path.isfile(path):
            os.remove(path)

    def load_cg_result(self, cg_id):
        cg_dir = os.path.join(self._investigations_dir, cg_id)
        yaml_path = os.path.join(cg_dir, "report.yaml")
        json_path = os.path.join(cg_dir, "report.json")
        if os.path.isfile(yaml_path):
            with open(yaml_path, "r") as f:
                if yaml:
                    return yaml.safe_load(f)
                return json.load(f)
        if os.path.isfile(json_path):
            with open(json_path, "r") as f:
                return json.load(f)
        return None

    # ─── Executions ───

    def get_execution_dir(self, cg_id, solution_idx):
        """获取 Execution Subagent 的工作目录，自动创建 backup/ 子目录。"""
        d = os.path.join(self._executions_dir, f"{cg_id}-solution-{solution_idx}")
        os.makedirs(os.path.join(d, "backup"), exist_ok=True)
        return d

    # ─── Approvals ───

    def save_approval(self, cg_id, solution_idx, approval_data):
        path = os.path.join(self._approvals_dir, f"{cg_id}-solution-{solution_idx}.json")
        with open(path, "w") as f:
            json.dump(approval_data, f, indent=2, ensure_ascii=False)

    def load_approval(self, cg_id, solution_idx):
        path = os.path.join(self._approvals_dir, f"{cg_id}-solution-{solution_idx}.json")
        if not os.path.isfile(path):
            return None
        with open(path, "r") as f:
            return json.load(f)

    def update_approval_status(self, cg_id, solution_idx, new_status):
        approval = self.load_approval(cg_id, solution_idx)
        if approval:
            approval["status"] = new_status
            self.save_approval(cg_id, solution_idx, approval)

    def list_approaching_expiry(self, now=None, warn_hours=24, ttl_hours=48):
        """列出已超过 warn_hours 但未到 ttl_hours 的 pending 审批。"""
        now_dt = _parse_iso(now) if now else datetime.now(timezone.utc)
        approaching = []
        pattern = os.path.join(self._approvals_dir, "CG-*-solution-*.json")
        for path in glob_mod.glob(pattern):
            with open(path, "r") as f:
                data = json.load(f)
            if data.get("status") != "pending_approval":
                continue
            sent_at = data.get("approval_sent_at")
            if not sent_at:
                continue
            elapsed = now_dt - _parse_iso(sent_at)
            if timedelta(hours=warn_hours) <= elapsed < timedelta(hours=ttl_hours):
                fname = os.path.basename(path).replace(".json", "")
                parts = fname.rsplit("-solution-", 1)
                approaching.append((parts[0], int(parts[1])))
        return approaching

    def list_expired_approvals(self, now=None, ttl_hours=48):
        now_dt = _parse_iso(now) if now else datetime.now(timezone.utc)
        expired = []
        pattern = os.path.join(self._approvals_dir, "CG-*-solution-*.json")
        for path in glob_mod.glob(pattern):
            with open(path, "r") as f:
                data = json.load(f)
            if data.get("status") != "pending_approval":
                continue
            sent_at = data.get("approval_sent_at")
            if sent_at and (now_dt - _parse_iso(sent_at)) > timedelta(hours=ttl_hours):
                fname = os.path.basename(path).replace(".json", "")
                parts = fname.rsplit("-solution-", 1)
                expired.append((parts[0], int(parts[1])))
        return expired


# ─── CLI ───

def main():
    import argparse

    parser = argparse.ArgumentParser(description="sre-agent 状态管理器")
    parser.add_argument("--state-dir", default=".sre-agent",
                        help="状态目录路径")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="初始化状态目录和空状态文件")
    sub.add_parser("status", help="打印当前状态摘要")

    args = parser.parse_args()
    mgr = StateManager(args.state_dir)

    if args.command == "init":
        state = mgr.load_state()
        print(f"状态目录已初始化: {args.state_dir}")
        print(f"  active CGs: {len(state['active_correlation_groups'])}")
        print(f"  completed CGs: {len(state['completed_correlation_groups'])}")
        print(f"  processed incidents: {len(state['processed_incident_ids'])}")
        print(f"  last poll: {state['last_poll_at']}")

    elif args.command == "status":
        state = mgr.load_state()
        print(json.dumps(state, indent=2, ensure_ascii=False))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
