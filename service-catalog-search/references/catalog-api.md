# Backstage Catalog REST API 速查（service-catalog-search 用）

> 官方文档：https://backstage.io/docs/features/software-catalog/software-catalog-api · https://backstage.io/docs/features/software-catalog/well-known-relations

## 鉴权

新 Backstage backend（RHDH 1.x 含）对 `/api/catalog/*` 要求 token（没 token → `401 Missing credentials`）。三种拿法：
- **生产**：在 RHDH `app-config` 里配一个 `static` external-access token 给本 skill 用：
  ```yaml
  backend:
    auth:
      externalAccess:
        - type: static
          options: { token: ${SERVICE_CATALOG_SEARCH_TOKEN}, subject: service-catalog-search }
  ```
  脚本用 `Authorization: Bearer ${RHDH_TOKEN}`。
- **本机开发**：用 guest provider 拿一个临时 token：`POST $RHDH_BASE_URL/api/auth/guest/refresh`（带 `x-requested-with: XMLHttpRequest` header）→ 响应里 `.backstageIdentity.token`。`catalog-query.sh` 没设 `RHDH_TOKEN` 时自动这么做。
- service account：org discovery 出来的 `User` + 一个有 catalog-read 权限的 token。

## 主要 endpoints

| 用途 | endpoint |
|---|---|
| 列实体（推荐，带过滤/游标分页）| `GET /api/catalog/entities/by-query?filter=<filter>&fields=<f1,f2,...>&limit=N&cursor=<cursor>`，返回 `{items,totalItems,pageInfo}` |
| 列实体（兼容旧版，已 deprecated）| `GET /api/catalog/entities?filter=<filter>&fields=<f1,f2,...>&limit=N&offset=M`，返回实体数组 |
| 按名查单个实体 | `GET /api/catalog/entities/by-name/<kind>/<namespace>/<name>` （如 `/by-name/component/default/subscription`、`/by-name/api/default/entitlement-check`）|
| 按 ref 查 | `GET /api/catalog/entities/by-refs`（POST `{"entityRefs":[...]}`）|
| 实体的祖先（含 Location）| `GET /api/catalog/entities/by-name/<kind>/<ns>/<name>/ancestry` |
| 列 Location | `GET /api/catalog/locations` |
| 注册一个 Location（手动）| `POST /api/catalog/locations` `{"type":"url","target":"https://..."}` |
| 刷新某实体 | `POST /api/catalog/refresh` `{"entityRef":"component:default/xxx"}` |

`by-query` 的分页响应使用 `pageInfo.nextCursor`。首页携带 `filter`、`fields`
和 `limit`；后续页携带 `cursor`（可继续携带 `fields` / `limit`），不再携带 `filter`。
官方契约规定 `filter` / `orderField` / `fullTextFilter` 与 `cursor` 互斥；同时传入时
只会使用 `cursor`，因为它已编码首页的查询上下文。不要把 `items` 误当成旧版实体数组。

## `filter` 语法

- 多条件 AND（同一个 `filter`）：`filter=kind=Component,spec.type=service` （逗号 = AND）
- 多个 OR：重复 `filter=` 参数：`filter=kind=Component&filter=kind=API`
- 嵌套字段用点：`filter=metadata.tags=push`（数组字段 = 「包含该值」）、`filter=spec.owner=group:default/team-x`、`filter=relations.partOf=system:default/foo`
- 存在性：`filter=metadata.annotations.backstage.io/kubernetes-id`（key 存在即可，不给值）
- `metadata.tags` 是数组——`filter=metadata.tags=layer-base-platform` 匹配 tags 里含该值的实体；多个 tag 用 `filter=metadata.tags=a,metadata.tags=b` 不行（那是 AND 的两个 metadata.tags=... 不合法）；要「同时含 a 和 b」用两个 `filter=` OR? 不——实际是同一 filter 里 `metadata.tags=a,metadata.tags=b` 表示「tags 含 a 且 tags 含 b」（每个键值对独立 AND）。OK。

> ⚠️ Backstage `filter` 不支持「数组里对象的字段」的深过滤（如 `spec.providesApis` 是字符串数组——这个 OK，能 `filter=spec.providesApis=entitlement-check`；但如果某字段是对象数组就不行）。本项目的 schema 故意都用扁平字符串数组（`tags`、`providesApis`、`consumesApis`、`dependsOn`），所以 `filter` 够用——这也是 ADR-002 选「能力 = `API` 实体 + tag」而不是「`a4x.io/provides` 对象数组」的原因之一。

## `fields` —— 只取需要的字段

`fields=metadata.name,metadata.title,metadata.tags,metadata.links,spec.type,spec.lifecycle,spec.owner,spec.system,relations` —— 减小 payload。不给 `fields` 则返回完整实体。

## `relations` —— 关系跳转（能力反查靠这个）

每个实体的 `relations` 字段是 `[{type, targetRef}, ...]`。well-known relations：
- `ownedBy` / `ownerOf` —— Component/API/... ↔ Group/User
- `partOf` / `hasPart` —— Component ∈ System、System ∈ Domain（也 Component ∈ Component 的「子组件」）
- `dependsOn` / `dependencyOf` —— Component → Component/Resource
- `providesApi` / `apiProvidedBy` —— Component → API（`apiProvidedBy`: API → 提供它的 Component）
- `consumesApi` / `apiConsumedBy` —— Component → API（`apiConsumedBy`: API → 消费它的 Component）

**能力反查的核心**：查到 `API` 实体 → 读 `relations` 里 `type=="apiProvidedBy"` 的 `targetRef`（如 `component:default/novu`）→ 那就是提供这个能力的服务 → 再查那个 Component 拿 `metadata.tags`（约束 `no-direct-*`）/ `spec.owner` / `metadata.links`（接入规格）/ `spec.system`（顺 `partOf` 到 System、再到 Domain）。

## 例子

```bash
T=$(curl -s -X POST "$RHDH/api/auth/guest/refresh" -H 'x-requested-with: XMLHttpRequest' | jq -r .backstageIdentity.token)
H="Authorization: Bearer $T"

# 列后端服务首屏（layer=base-platform；全量请用 catalog-query.sh list-services）
curl -sG -H "$H" "$RHDH/api/catalog/entities/by-query" \
  --data-urlencode 'filter=kind=Component,spec.type=service,metadata.tags=layer-base-platform' \
  --data-urlencode 'fields=metadata.name,spec.owner,spec.lifecycle' \
  --data-urlencode 'limit=100' | jq -r '.items[].metadata.name'

# 能力反查：谁提供 entitlement-check
curl -s -H "$H" "$RHDH/api/catalog/entities/by-name/api/default/entitlement-check" | jq '.relations[] | select(.type=="apiProvidedBy") | .targetRef'

# 某服务的完整条目 + owner Group
curl -s -H "$H" "$RHDH/api/catalog/entities/by-name/component/default/subscription" | jq '{name:.metadata.name, type:.spec.type, layer:(.metadata.tags|map(select(startswith("layer-")))|.[0]), owner:.spec.owner, provides:.spec.providesApis, deps:.spec.dependsOn}'

# 列 API 首屏（=能力）+ 提供方（全量请用 catalog-query.sh list-capabilities）
curl -sG -H "$H" "$RHDH/api/catalog/entities/by-query" \
  --data-urlencode 'filter=kind=API' \
  --data-urlencode 'fields=metadata.name,metadata.tags,spec.system,relations' \
  --data-urlencode 'limit=100' | jq -r '.items[] | "\(.metadata.name)\tprovidedBy=\(.relations//[]|map(select(.type=="apiProvidedBy"))|map(.targetRef)|join(","))"'
```
