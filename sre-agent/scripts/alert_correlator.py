#!/usr/bin/env python3
"""
告警关联器。
规则（按 spec §5.1，修订版）：
  1. 标题标准化后精确匹配 → 合并
  2. ±5min 内同故障实体 → 关联
  3. 与 completed CG 标题匹配 → 标记复发
"""

import re
from datetime import datetime, timezone, timedelta

# ─── 标题标准化 ───

_RE_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_RE_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[Z\+\-\d:]*")
_RE_POD_SUFFIX = re.compile(r"-[0-9a-f]{6,10}-[0-9a-z]{5}")
_RE_NUMBERS = re.compile(r"\b\d{3,}\b")
_RE_MULTI_SPACE = re.compile(r"\s+")


def normalize_title(title):
    """去掉动态部分，返回标准化后的小写标题。"""
    t = title
    t = _RE_UUID.sub("", t)
    t = _RE_TIMESTAMP.sub("", t)
    t = _RE_POD_SUFFIX.sub("", t)
    t = _RE_NUMBERS.sub("", t)
    t = _RE_MULTI_SPACE.sub(" ", t).strip().lower()
    return t


# ─── K8s 资源名称标准化 ───

_RE_RS_POD_SUFFIX = re.compile(r"-[0-9a-f]{8,10}-[0-9a-z]{5}$")   # ReplicaSet hash + Pod random suffix
_RE_RS_HASH = re.compile(r"-[0-9a-f]{8,10}$")                       # ReplicaSet hash only
_RE_STATEFULSET_ORDINAL = re.compile(r"-\d{1,3}$")                  # StatefulSet ordinal (-0, -1, -12)
_RE_DAEMONSET_SUFFIX = re.compile(r"-[a-z0-9]{5}$")                 # DaemonSet 5-char suffix


def _normalize_k8s_name(name):
    """
    将 Pod/ReplicaSet 名称标准化为 Deployment/StatefulSet 名称。

    优先级（从最具体到最通用）：
    1. Pod 全名: deployment-<rs-hash>-<random> → deployment
    2. ReplicaSet 名: deployment-<rs-hash> → deployment
    3. StatefulSet Pod 名: statefulset-<ordinal> → statefulset
    4. DaemonSet Pod 名: daemonset-<random> → daemonset
    5. 无后缀: 原样返回
    """
    # 1. ReplicaSet hash + Pod random suffix: app-7b8f9c6d4f-x2k9z → app
    result = _RE_RS_POD_SUFFIX.sub("", name)
    if result != name:
        return result

    # 2. ReplicaSet hash only: app-7b8f9c6d4f → app
    result = _RE_RS_HASH.sub("", name)
    if result != name and len(result) > 0:
        return result

    # 3. StatefulSet ordinal: redis-cluster-0 → redis-cluster
    # 避免把 "gpu-infer-v2" 变成 "gpu-infer-v"（-v 后接数字是版本号）
    result = _RE_STATEFULSET_ORDINAL.sub("", name)
    if result != name and len(result) > 0:
        if result.endswith("-v"):
            return name  # 保留版本号如 gpu-infer-v2
        return result

    # 4. DaemonSet suffix: node-exporter-abc12 → node-exporter
    result = _RE_DAEMONSET_SUFFIX.sub("", name)
    if result != name and len(result) > 0:
        return result

    return name


# ─── 故障实体提取 ───

_RE_ALERTMANAGER_LABELS = re.compile(
    r"- (\w+)\s*=\s*(.+)"
)

# 需要从 labels 中提取的所有 key
_ALL_LABEL_KEYS = (
    "alertname", "namespace", "cluster",
    "deployment", "statefulset", "daemonset", "pod",
    "dbidentifier", "domain_name", "container",
)


def _extract_labels_from_details(details):
    """从 alert details dict 中解析 Alertmanager labels。"""
    labels = {}

    # 检查 details.labels 嵌套结构（如 {"labels": {"alertname": "...", "namespace": "..."}}）
    nested_labels = details.get("labels", {})
    if isinstance(nested_labels, dict):
        for key in _ALL_LABEL_KEYS:
            if key in nested_labels:
                labels[key] = nested_labels[key]
        if labels.get("alertname"):
            return labels

    # details 可能直接包含 label 字段（如 {"alertname": "...", "namespace": "..."}）
    for key in _ALL_LABEL_KEYS:
        if key in details:
            labels[key] = details[key]
    if labels.get("alertname"):
        return labels

    # 从 firing 文本中解析
    firing_text = details.get("firing", "")
    if firing_text:
        for m in _RE_ALERTMANAGER_LABELS.finditer(firing_text):
            key = m.group(1)
            if key in _ALL_LABEL_KEYS:
                labels[key] = m.group(2).strip()

    return labels


def _build_service_entity_from_labels(labels):
    """
    构建 service-level entity（不含 alertname），用于跨类型匹配。

    返回: "<resource_name>|<cluster>|<namespace>" 或 None
    """
    # 提取资源名称（优先级: deployment > statefulset > daemonset > pod标准化 > dbidentifier > domain_name）
    resource_name = None
    for key in ("deployment", "statefulset", "daemonset"):
        if labels.get(key):
            resource_name = labels[key]
            break

    if not resource_name and labels.get("pod"):
        resource_name = _normalize_k8s_name(labels["pod"])

    if not resource_name:
        for key in ("dbidentifier", "domain_name"):
            if labels.get(key):
                resource_name = labels[key]
                break

    if not resource_name:
        return None

    parts = [resource_name]
    if labels.get("cluster"):
        parts.append(labels["cluster"])
    if labels.get("namespace"):
        parts.append(labels["namespace"])

    return "|".join(parts)


def _build_entity_from_labels(labels):
    """
    从解析出的 labels 构建故障实体标识。

    返回格式:
    - 有 service_entity: "<service_entity>|<cluster>|<namespace>"
    - 仅有 alertname: "<alertname>|<cluster>|<namespace>" (原有行为)
    - 无 alertname: None
    """
    if not labels.get("alertname"):
        return None

    # 先尝试提取 service entity
    service_entity = _build_service_entity_from_labels(labels)
    if service_entity:
        return service_entity

    # fallback 到原有行为: alertname|cluster|namespace
    parts = [labels["alertname"]]
    if labels.get("cluster"):
        parts.append(labels["cluster"])
    if labels.get("namespace"):
        parts.append(labels["namespace"])

    return "|".join(parts)


def _extract_fault_entity(inc):
    """
    从 incident 提取故障实体标识。

    优先级：
    1. 从 alert_details labels 提取 (deployment > statefulset > pod > dbidentifier > domain_name)
    2. 从 embedded alerts 的 details 提取
    3. fallback: alertname|cluster|namespace (若有 alertname 但无 service entity)
    4. fallback: 标题标准化值
    """
    # 1. 从 alert_details 中提取
    alert_details = inc.get("alert_details", {})
    if alert_details:
        labels = _extract_labels_from_details(alert_details)
        entity = _build_entity_from_labels(labels)
        if entity:
            return entity

    # 2. 从 embedded alerts 中提取
    for alert in inc.get("alerts", []):
        body = alert.get("body", {})
        details = body.get("details", {})
        if details:
            labels = _extract_labels_from_details(details)
            entity = _build_entity_from_labels(labels)
            if entity:
                return entity

    # 3. fallback 到标题标准化值
    return normalize_title(inc.get("title", ""))


def _extract_service_entity(inc):
    """
    从 incident 提取 service-level 故障实体标识（不含 alertname）。

    用于跨告警类型合并判断：同一 service_entity + 不同 alertname → 同一故障。

    返回: "<deployment_or_resource>|<cluster>|<namespace>" 或 None
    """
    alert_details = inc.get("alert_details", {})
    if alert_details:
        labels = _extract_labels_from_details(alert_details)
        result = _build_service_entity_from_labels(labels)
        if result:
            return result

    for alert in inc.get("alerts", []):
        body = alert.get("body", {})
        details = body.get("details", {})
        if details:
            labels = _extract_labels_from_details(details)
            result = _build_service_entity_from_labels(labels)
            if result:
                return result

    return None


# ─── 告警关联 ───

_WINDOW = timedelta(minutes=5)
_CROSS_TYPE_WINDOW = timedelta(minutes=30)
_RECURRENCE_WINDOW = timedelta(hours=2)


def _parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _same_fault_entity(inc, cg_or_group):
    """判断 incident 与 CG/group 是否属于同一故障实体。"""
    inc_entity = _extract_fault_entity(inc)
    if isinstance(cg_or_group, dict):
        ref_entity = cg_or_group.get("fault_entity", "")
        return inc_entity == ref_entity and inc_entity != ""
    return False


def _within_window(inc, cg_or_group):
    inc_time = _parse_time(inc["created_at"])
    ref_time_str = cg_or_group.get("created_at", "")
    if not ref_time_str:
        return False
    ref_time = _parse_time(ref_time_str)
    return abs(inc_time - ref_time) <= _WINDOW


def _same_service_entity(inc, cg_or_group):
    """判断 incident 与 CG/group 是否属于同一 service entity（跨类型匹配）。"""
    inc_entity = _extract_service_entity(inc)
    if not inc_entity:
        return False
    if isinstance(cg_or_group, dict):
        ref_entity = cg_or_group.get("service_entity")
        if not ref_entity:
            return False
        return inc_entity == ref_entity
    return False


def _within_cross_type_window(inc, cg_or_group):
    inc_time = _parse_time(inc["created_at"])
    ref_time_str = cg_or_group.get("created_at", "")
    if not ref_time_str:
        return False
    ref_time = _parse_time(ref_time_str)
    return abs(inc_time - ref_time) <= _CROSS_TYPE_WINDOW


def _titles_match(inc, group_incidents):
    norm = normalize_title(inc.get("title", ""))
    return any(normalize_title(i.get("title", "")) == norm for i in group_incidents)


def _detect_recurrence(inc, completed_cgs):
    """
    检查 incident 是否与某个 completed CG 的标题标准化匹配。

    Returns:
        dict {"cg_id": str, "is_recurrence": True} 若匹配，否则 None。

    时间窗口规则：
    - 若 completed CG 有 completed_at，则只在 2h 内匹配
    - 若无 completed_at，则不限时间（向后兼容）
    """
    norm = normalize_title(inc.get("title", ""))
    inc_created_at = inc.get("created_at")

    for cg_id, cg in completed_cgs.items():
        # 时间窗口检查（若有 completed_at）
        completed_at_str = cg.get("completed_at")
        if completed_at_str and inc_created_at:
            inc_time = _parse_time(inc_created_at)
            completed_time = _parse_time(completed_at_str)
            if inc_time - completed_time > _RECURRENCE_WINDOW:
                continue

        # completed CG 可能有 normalized_title 或 incidents 列表
        cg_titles = set()
        if "normalized_title" in cg:
            cg_titles.add(cg["normalized_title"])
        for cg_inc in cg.get("incidents", []):
            if isinstance(cg_inc, dict):
                cg_titles.add(normalize_title(cg_inc.get("title", "")))
            elif isinstance(cg_inc, str):
                # incident ID string, 无法比较标题
                pass
        # 也比较 CG 自身的 title 字段
        if "title" in cg:
            cg_titles.add(normalize_title(cg["title"]))
        if norm in cg_titles:
            return {"cg_id": cg_id, "is_recurrence": True}
    return None


def correlate_incidents(new_incidents, active_cgs=None, completed_cgs=None):
    """
    对新告警执行关联分组。

    Args:
        new_incidents: 新告警列表
        active_cgs: 活跃的 CG dict {cg_id: cg_data}
        completed_cgs: 已完成的 CG dict {cg_id: cg_data}，用于复发检测

    Returns:
        list of groups, each:
        {
            "group_id": "new-N" | None,
            "correlates_to": "CG-X" | None,
            "incidents": [incident, ...],
            "service": str,
            "fault_entity": str,
            "service_entity": str | None,
            "recurrence_of": "CG-X" | None,
            "is_recurrence": bool,
        }
    """
    active_cgs = active_cgs or {}
    completed_cgs = completed_cgs or {}
    groups = []
    assigned = set()

    for inc in new_incidents:
        if inc["id"] in assigned:
            continue

        inc_entity = _extract_fault_entity(inc)
        inc_service_entity = _extract_service_entity(inc)

        # 1. 尝试关联到已有 active CG
        matched_cg = None
        for cg_id, cg in active_cgs.items():
            # 确保 active CG 有 fault_entity 字段（兼容旧数据）
            if "fault_entity" not in cg:
                cg["fault_entity"] = cg.get("service", "")
            if _same_fault_entity(inc, cg) and _within_window(inc, cg):
                matched_cg = cg_id
                break

        # 1.5 跨类型匹配：same service entity + 30min window
        if not matched_cg:
            for cg_id, cg in active_cgs.items():
                # 确保 CG 有 service_entity（兼容旧数据：尝试从 fault_entity 推断或跳过）
                cg_service_entity = cg.get("service_entity")
                if not cg_service_entity:
                    continue
                if _same_service_entity(inc, cg) and _within_cross_type_window(inc, cg):
                    matched_cg = cg_id
                    break

        if matched_cg:
            group_incidents = [inc]
            assigned.add(inc["id"])
            cg_data = active_cgs[matched_cg]
            for other in new_incidents:
                if other["id"] in assigned:
                    continue
                if _same_fault_entity(other, cg_data) and (
                    _within_window(other, cg_data)
                    or _titles_match(other, group_incidents)
                ):
                    group_incidents.append(other)
                    assigned.add(other["id"])

            # 复发检测
            recurrence = _detect_recurrence(inc, completed_cgs)

            groups.append({
                "group_id": None,
                "correlates_to": matched_cg,
                "incidents": group_incidents,
                "service": inc.get("service", {}).get("summary", ""),
                "fault_entity": inc_entity,
                "service_entity": inc_service_entity,
                "recurrence_of": recurrence["cg_id"] if recurrence else None,
                "is_recurrence": recurrence["is_recurrence"] if recurrence else False,
            })
            continue

        # 2. 尝试合并到本批次已有组
        merged = False
        for g in groups:
            if g["correlates_to"]:
                continue
            if g["fault_entity"] == inc_entity and inc_entity != "":
                if _within_window(inc, {"created_at": g["incidents"][0]["created_at"]}):
                    g["incidents"].append(inc)
                    assigned.add(inc["id"])
                    merged = True
                    break
                if _titles_match(inc, g["incidents"]):
                    g["incidents"].append(inc)
                    assigned.add(inc["id"])
                    merged = True
                    break

        # 2b. 跨类型合并：same service entity + 30min window（在本批次已有组中）
        if not merged:
            for g in groups:
                if g["correlates_to"]:
                    continue
                g_service_entity = g.get("service_entity")
                if not g_service_entity:
                    continue
                if (
                    inc_service_entity
                    and inc_service_entity == g_service_entity
                    and _within_cross_type_window(inc, {"created_at": g["incidents"][0]["created_at"]})
                ):
                    g["incidents"].append(inc)
                    assigned.add(inc["id"])
                    merged = True
                    break

        if not merged:
            assigned.add(inc["id"])

            # 复发检测
            recurrence = _detect_recurrence(inc, completed_cgs)

            groups.append({
                "group_id": f"new-{len(groups) + 1}",
                "correlates_to": None,
                "incidents": [inc],
                "service": inc.get("service", {}).get("summary", ""),
                "fault_entity": inc_entity,
                "service_entity": inc_service_entity,
                "recurrence_of": recurrence["cg_id"] if recurrence else None,
                "is_recurrence": recurrence["is_recurrence"] if recurrence else False,
            })

    return groups
