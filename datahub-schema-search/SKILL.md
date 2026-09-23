---
name: datahub-schema-search
description: >
  通过自然语言搜索 DataHub 中的表结构元数据。当用户询问数据库表、表结构、字段定义，
  或想查找某个主题相关的表时使用此 skill（例如 "订单表"、"设备激活相关的表"、
  "amazon数据库有哪些表"、"analytics.orders有哪些字段"）。
  即使用户只是随口提到需要找表或看 schema，也应使用此 skill。
---

# DataHub 表结构搜索

## Description

通过 datahub-schema-search 服务的 REST API，帮助用户以自然语言搜索和浏览 DataHub 中的表结构元数据。支持语义搜索、精确查表、浏览数据库和表列表。

服务地址：`https://datahub-schema-search.addx.live`

Swagger API 文档：https://datahub-schema-search.addx.live/docs

### API 接口

```
GET /api/search?query=订单表&top_k=10        # 语义搜索
GET /api/tables/{database.table}              # 精确查询某张表
GET /api/databases                            # 列出所有数据库
GET /api/tables                               # 列出所有表
GET /api/tables?database=amazon               # 列出指定数据库的表
GET /health                                   # 健康检查
```

使用 `curl` 调用这些接口，返回 JSON 格式数据。

## Rules

1. **只读操作** — 仅允许调用 GET 接口查询数据，禁止调用 `POST /api/sync` 触发同步（数据由后台定时任务自动同步，手动触发尤其是全量同步会造成较大负载）
2. **用户要求同步时** — 告知数据会自动更新，无需手动操作
3. **URL 编码** — 中文查询参数必须进行 URL 编码后再传入 curl
4. **接口选择** — 根据用户意图选择最合适的接口：
   - 模糊的、按主题的查询 → `GET /api/search`
   - 指定了具体表名 → `GET /api/tables/{full_name}`
   - "有哪些数据库？" → `GET /api/databases`
   - "某个数据库有哪些表？" → `GET /api/tables?database=X`
5. **搜索数量** — 默认 `top_k=10`；如果用户明确指定了返回数量，则按用户指定的值设置 top_k
6. **结果展示** — 多表结果先展示摘要列表（表名 + 简要说明），再询问用户是否需要查看详情；单表结果展示完整字段信息（字段名、类型、描述）；如果用户在写 SQL，主动推荐相关的字段名

## Examples

### Good Example

**按主题搜索** — 用户："帮我找订单相关的表"

```bash
curl -s 'https://datahub-schema-search.addx.live/api/search?query=%E8%AE%A2%E5%8D%95&top_k=10'
```

→ 按分类展示匹配到的表摘要列表，询问用户是否需要查看某张表的详细字段。

**查看指定表** — 用户："vip.dwd_vip_order_cancel_di 有哪些字段？"

```bash
curl -s 'https://datahub-schema-search.addx.live/api/tables/vip.dwd_vip_order_cancel_di'
```

→ 展示完整的字段列表、类型和描述。

**浏览数据库** — 用户："有哪些数据库？"

```bash
curl -s 'https://datahub-schema-search.addx.live/api/databases'
```

**查看某数据库的表** — 用户："vip 数据库下有哪些表？"

```bash
curl -s 'https://datahub-schema-search.addx.live/api/tables?database=vip'
```

### Bad Example

用户："帮我同步一下数据"

```bash
# 错误：禁止调用同步接口
curl -X POST 'https://datahub-schema-search.addx.live/api/sync'
```

应回复：数据由后台定时任务自动同步，无需手动触发。
