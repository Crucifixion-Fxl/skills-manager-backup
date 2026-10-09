"""Validate native shutdown evidence and coalesce it into current ready feedback.

This reader never publishes an old-generation stopping event or edits native
journals. Current route, source authority and readiness gates live in recover().
"""
from buzz_feishu_group_sync import _nip01_event_verified

STOPPING = "Agent 正在停止或重启，当前任务可能中断；下次启动成功后会在本话题自动继续，请勿重复提交。"


def validate(snapshot):
    values = snapshot.get("shutdown_notices")
    if (not isinstance(values, dict) or len(values) > 4096
            or values and snapshot["phase"] != "stopping"):
        raise ValueError("invalid shutdown notice inventory")
    routes = {(s["route"]["channel"], s["route"]["root"])
              for sources in snapshot["input_sources"].values() for s in sources}
    for key, value in values.items():
        if not isinstance(value, dict) or set(value) != {"route", "notice"}:
            raise ValueError("invalid shutdown notice")
        route = value["route"]
        if (not isinstance(route, dict) or set(route) != {"channel", "root"}
                or (route["channel"], route["root"]) not in routes
                or key != route["channel"] + ":" + route["root"]):
            raise ValueError("invalid shutdown notice route")
        notice = value["notice"]
        if notice is None:
            continue
        if not isinstance(notice, dict) or set(notice) != {"event", "delivered"} or type(notice["delivered"]) is not bool:
            raise ValueError("invalid shutdown notice delivery")
        event = notice["event"]
        tags = [["h", route["channel"]], ["e", route["root"], "", "root"], ["e", route["root"], "", "reply"],
                ["buzz:recovery-lifecycle", snapshot["generation"], "stopping"]]
        if (not isinstance(event, dict) or event.get("kind") != 9
                or event.get("pubkey") != snapshot["agent_pubkey"] or event.get("tags") != tags
                or event.get("content") != STOPPING or not _nip01_event_verified(event)):
            raise ValueError("invalid signed shutdown notice")


def ready_message(agent_name, snapshots, current_generation, channel, root, work_ids):
    """Describe only interrupted originals selected for this authorized route."""
    relevant = []
    selected = set(work_ids)
    for state in snapshots:
        if state["generation"] == current_generation:
            continue
        if any(state["receipts"][input_id] == "active" and any(s["event_id"] in selected for s in sources)
               for input_id, sources in state["input_sources"].items()):
            relevant.append(state)
    explanations = []
    if any(state["phase"] != "stopping" for state in relevant):
        explanations.append("此前任务异常中断（未记录正常停机）")
    planned = [state for state in relevant if state["phase"] == "stopping"]
    if planned:
        pending = [state["shutdown_notices"].get(channel + ":" + root, {}).get("notice") for state in planned]
        explanations.append("此前任务因停止或重启中断" +
                            ("，停机提示未确认送达" if any(not n or not n["delivered"] for n in pending) else ""))
    reason = "；".join(explanations)
    return (f"{agent_name} 启动成功。{reason}；现在正在自动续接本话题。"
            "会先核对已完成的动作和产物，再继续剩余任务，无需手动发送 continue。")
