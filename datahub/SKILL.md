---
name: datahub
description: DataHub 数据目录操作。搜索数据资产（表、列、Dashboard、Pipeline）、查看数据血缘、获取 Schema 元数据、浏览数据目录时使用。
---

# datahub

通过 DataHub 搜索数据资产、查看元数据与血缘关系、浏览数据目录。

## Description

适用场景：搜索表/列/Dashboard/Pipeline、查看表 Schema 和字段描述、追踪数据血缘（上下游依赖）、浏览数据目录层级、查看数据 Ownership。

- **内部地址**：`https://datahub.addx.live`
- **API 架构**：GraphQL（主，`/api/graphql`）+ REST OpenAPI（补充）
- **GraphQL Playground**：`https://datahub.addx.live/api/graphiql`
- **Swagger UI**：`https://datahub.addx.live/openapi/swagger-ui/index.html`

| 变量 | 说明 | 必需 |
|------|------|------|
| `DATAHUB_URL` | DataHub 地址（`https://datahub.addx.live`） | 是 |
| `DATAHUB_TOKEN` | Personal Access Token（Settings → Access Tokens → Generate） | 是 |

认证方式：所有请求使用 `Authorization: Bearer $DATAHUB_TOKEN` 头。Token 在 DataHub UI → Settings → Access Tokens 生成，需要 "Generate Personal Access Tokens" 权限。

> API 用法（GraphQL query/mutation 语法、REST 端点）通过 Context7 MCP 查询 DataHub 官方文档。

## Rules

### 公司特定约定

- **DataHub 是元数据权威来源** — Superset Dashboard 元信息、Dagster Pipeline 血缘等下游工具均以 DataHub 为准
- **URN 格式约定** — `urn:li:dataset:(urn:li:dataPlatform:{platform},{name},{env})`，`env` 统一使用 `PROD`
- **Platform 命名** — 公司内常见 platform：`athena`、`postgres`、`s3`、`kafka`、`dagster`

### 操作红线

- **只读操作为主** — 搜索、查询、浏览均安全；写操作（tag/owner 变更）必须告知用户确认
- 搜索分页上限 **10,000 条** — 超过需用 `scrollAcrossEntities`（基于 scrollId 翻页）
- GraphQL 字段按需选取，避免请求过多嵌套字段导致超时

### 常见工作流

1. **找表查 Schema**：搜索表名 → 取 URN → 查 schemaMetadata.fields → 查看字段名、类型、描述
2. **追踪数据血缘**：确认实体 URN → 查上游（UPSTREAM）→ 查下游（DOWNSTREAM）
3. **数据资产盘点**：分页遍历 DATASET → 用 scrollId 翻页 → 统计 platform/owner 分布

## Examples

### Bad

```
搜索时不指定 types 过滤 → 结果混杂各种实体类型，难以使用
请求 count 设 100+ → 响应慢且多数结果无用
未选取具体字段 → 只拿到 URN，缺少 name/platform/description
```

### Good

```
搜索时指定 types: [DATASET] → 结果精准
count 设 10~20 合理分页 → 响应快
选取 name, platform.name, properties.description → 结果可读
拿到 URN 后再查详情（schema、ownership、lineage）→ 分步操作，按需深入
```
