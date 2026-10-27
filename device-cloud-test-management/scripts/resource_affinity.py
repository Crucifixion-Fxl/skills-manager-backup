"""Pure affinity validation shared by preflight and managed Job readback."""

from __future__ import annotations

TOPOLOGY_FIELDS = {
    "belong_to_host": "belongToHost",
    "belong_to_cabinet": "belongToCabinet",
}


def normalize_constraints(value: object, resource_names: object) -> list[dict]:
    """Canonicalize supported constraints; never silently drop one."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("资源亲和约束必须是 JSON 数组")
    known_names = set(resource_names)
    normalized = {}
    for constraint in value:
        if not isinstance(constraint, dict) or set(constraint) != {
            "type", "topologyKey", "resources"
        }:
            raise ValueError("每条亲和约束必须且只能包含 type、topologyKey、resources")
        if constraint["type"] != "AFFINITY":
            raise ValueError("当前统一入口仅支持 AFFINITY；不支持的约束不会被忽略")
        topology = constraint["topologyKey"]
        if not isinstance(topology, str) or topology not in TOPOLOGY_FIELDS:
            raise ValueError("亲和拓扑仅支持 belong_to_host / belong_to_cabinet")
        names = constraint["resources"]
        if (
            not isinstance(names, list) or len(names) < 2
            or any(not isinstance(name, str) or not name.strip() for name in names)
            or len(set(names)) != len(names)
            or not set(names).issubset(known_names)
        ):
            raise ValueError("亲和约束必须引用至少两个不同的、已声明的逻辑资源")
        names = sorted(names)
        normalized[(topology, tuple(names))] = {
            "type": "AFFINITY", "topologyKey": topology, "resources": names,
        }
    return [normalized[key] for key in sorted(normalized)]


def validate_assignment(constraints: list[dict], allocations_by_alias: dict) -> bool:
    """Reject conflicting partial bindings; False means not fully bound yet."""
    complete = True
    for constraint in constraints:
        field = TOPOLOGY_FIELDS[constraint["topologyKey"]]
        values = set()
        for name in constraint["resources"]:
            resource = allocations_by_alias.get(name)
            if resource is None:
                complete = False
                continue
            if not isinstance(resource, dict):
                raise ValueError("亲和资源绑定结构无效")
            value = resource.get(field)
            if type(value) not in (str, int) or not str(value).strip():
                raise ValueError(f"亲和资源缺少 {constraint['topologyKey']}，无法核验")
            values.add(str(value))
        if len(values) > 1:
            raise ValueError(f"实际资源不满足 {constraint['topologyKey']} 亲和约束")
    return complete


def validate_affinity_counts(constraints: list[dict], resources: list[dict]) -> None:
    """The existing CLI binds one resource per alias; reject unsupported counts."""
    if not constraints:
        return
    for resource in resources:
        count = resource.get("count", 1)
        if type(count) is not int or count != 1:
            raise ValueError("亲和约束当前要求每个逻辑资源 count=1；未创建 TestPlan")
