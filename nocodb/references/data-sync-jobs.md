# 数据同步到数仓：data_sync.jobs

通过在 NocoDB `data_sync` 项目的 `jobs` 表新增一行记录，把任意源（NocoDB / JDBC / Athena 等）的数据按天或按小时同步到目标存储（默认 `datalake`，即 Iceberg 数据湖）。沿用此机制即可把上传到 NocoDB 的业务表自助接入数仓，避免再走"通知数仓团队加同步"的人工流程。

## 红线（先看！— 全文唯一红线清单，9 项）

> 本节是 data_sync.jobs 注册的全部红线。SKILL.md 红线节是同一份的入口必看精简版（每条标注 `→ 红线 #N`）；文档其它位置不再独立列红线，避免漂移。

1. **本 skill 严禁任何 DELETE / DROP 操作**：不得 `DELETE` `data_sync.jobs` 任意行、不得删 `connections`、不得删源表、不得删 dest_database/dest_table 中已存的历史数据。停用一个任务请改 `support=""`（支持环境置空）或加 `tags=弃用`，由数仓团队评估后再处理。即使用户明确要求"删掉旧 job 行"也必须拒绝，给出"改 tags=弃用"的替代方案。
2. **所有关键字段都要用户确认后再填**：`source_table_name` / `connection` / `dest_database` / `dest_connection` / `partition_def` / `schedule` / `support` 这七项，**禁止 agent 自己猜值**。先把字段含义和可选值列出，让用户逐项确认或输入；用户没回答之前不要 POST。
3. **Selector 类字段必须以菜单形式展示**：`partition_def` / `support` 是要用户选的 SingleSelect / MultiSelect，**不能把可选值写死在 prompt 里要用户自己抄**——必须用编号列表（`[1] past day  [2] past hour`）让用户选。可选值清单见后文"选择器字段菜单"。其余字段全部用默认值，**不询问用户**：`allocate_cpu=1 Core` / `allocate_memory=4 GiB` / `_sync_regions=空` / `timeout=3600` / `partition_start=2025-07-01` / `tags=空`。
4. **`dest_table` 由 agent 按规则自动生成**（不再单独问用户，但要在最终确认表里展示让用户一并确认/改名）：
   - 取源表的"业务可读名"作为 basename（NocoDB 源用 `md_xxx` 对应的表 title；JDBC 源去掉 schema 前缀；Athena 源同理），全小写、非字母数字下划线一律换成 `_`。
   - 拼接：`ods_<basename>` + 后缀。
   - **`past day`（日级）**：无后缀（如 `ods_user_profile`）。
   - **`past hour`（时级）**：默认加 `_hf`（hourly full，时级一般是全量同步）；只有用户明说"增量"时才用 `_hi`。
   - 自动生成结果若不合 Athena 命名（regex `^[a-z_][a-z0-9_]*$`），直接报错让用户给一个合规 basename，不要自动改名。
5. **`job_args` 由 agent 按 `partition_def` 自动选模板**（不再单独问用户，最终确认表展示让用户确认/调整）：
   - **`past day`** → `{"iceberg_pre_sql": ["delete from {dest_table} where dt between date('{dt_start}') and date('{dt_end}')"]}`
   - **`past hour`** → `{"iceberg_pre_sql": ["delete from {dest_table} where dt between date_parse('{dt_start}','%Y-%m-%d-%H') and date_parse('{dt_end}','%Y-%m-%d-%H')"]}`
   - **JDBC 源 + MySQL tinyint(1) 问题**：在上一模板里加 `"jdbc_properties": {"tinyInt1isBit": false}`。
   - **特殊场景**（Athena→Redis、custom transform_sql 等）：仅当用户明确指出特殊处理时才偏离默认；不要主动追问。
6. **`dest_database` / `dest_table` 不可改名**：底层 Iceberg 表已建好，改名会让旧分区数据孤立。需要迁移时新建任务行，旧行打 `tags=弃用`，**不能 DELETE 旧行**。
7. **`dest_database` 不允许凭空造新域**：必须在已有 schema 列表里选。用户给一个不存在的域时，先把现有列表查给他看（curl 见"dest_database 业务域参考"小节）让他在已有项里挑；都不满足则引导用户走"新增数仓 schema"流程提 issue（建议走 `gitlab-issue-sop`），schema 建好后再回来注册同步任务，**严禁**往不存在的 schema 写 jobs 行。
8. **NocoDB 源的 `source_table_name`** 必须是 `md_xxxx` 表 id（不是 title），填错任务每次都会失败。Agent 主动用 Meta API 把"项目+表 title"翻译成 `md_xxx` 后再填。
9. **FK 列名 + jobs 表 id 漂移防御**：`nc_81dn__connections_id*` 是 NocoDB 自动生成的内部列名；jobs 表本身的内部 id（如 `md_4prd5u3stmz9ah`）会因表重建而变。POST 前**先反查 jobs 表 id**（用项目 `data_sync` 的 tables 列表 + filter title=jobs 拿 `md_xxx`），再 `GET /api/v1/db/meta/tables/<jobs_table_id>` 确认 FK 列名仍存在；不要把 jobs 表 id 写死。完整反查命令见"FK 列名"callout。

## 项目和表

- **项目**：`data_sync`
- **表**：`jobs`（同步任务清单）
- **辅助表**：`connections`（数据源/数据湖连接清单，按 `name` 唯一）
- **同步引擎**：按 `schedule` cron 周期触发，读取本表，按 `partition_def` 切窗后从 `connection` 拉数 → 经 `job_args` 处理 → 写入 `dest_connection` 的 `dest_database.dest_table`

## 选择器字段菜单（必须展示给用户选）

向用户征集这些字段时，**必须用编号菜单**，不要把选项藏在描述里。

```
partition_def（分区粒度，必选 1 个）：
  [1] past day    （日级，对应 schedule 例如 5 0 * * *；dest_table 无后缀，agent 自动生成 ods_<basename>）
  [2] past hour   （时级，对应 schedule 例如 5 * * * *；dest_table 必须加后缀，agent 默认生成 ods_<basename>_hf，增量改 _hi）

support（部署环境，可多选，逗号分隔）：
  [1] prod
  [2] staging
  [3] dev
  推荐：prod,staging（除非用户只想跑某个环境）
```

**不询问用户、用固定默认值的字段**（agent 直接填，不展示菜单）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `allocate_cpu` | `1 Core` | 标准任务足够；只有遇到 OOM/超时再让用户改 |
| `allocate_memory` | `4 GiB` | 同上 |
| `_sync_regions` | 空（null） | 仅主区同步；跨区是少数场景，默认不开 |
| `timeout` | `3600` | 秒 |
| `partition_start` | `2025-07-01` | 历史回填起点，沿用现表统一约定，不要改 |
| `tags` | 空（null） | 新建任务一律不打 tag；`弃用` 是停用旧任务专用，不在新增流程里用 |

非 selector 但仍属"必须用户填写、agent 不得替用户决定"的字段：
`source_table_name` / `dest_database` / `connection`（按 name 解析为 Id） / `dest_connection`（按 name 解析为 Id） / `schedule`（cron）。

**Agent 自动生成、用户仅在最终确认表 确认的字段**：
- `dest_table` — 按红线 #4 规则从 source basename + `partition_def` 推导，用户可改名覆盖。
- `job_args` — 按红线 #5 规则从 `partition_def` 选 day/hour 删分区模板，用户有特殊处理（custom SQL / Redis 等）才覆盖。

## dest_database 业务域参考（向用户展示）

询问 `dest_database` 时，把常见域 + 含义贴出来给用户挑；如果用户不确定，提议运行查询列出当前所有已用域让其参考。

```
常见业务域（按使用频度排序）：
  device              设备域（IoT/摄像头设备数据）
  events              埋点事件域
  analytics           埋点分析域（埋点相关，与 events 同属埋点体系）
  a4xmanage           a4x 管理后台域
  factory             工厂/产线域
  vip                 增值业务域（VIP/订阅）
  user                用户域（账号/身份/资料）
  ecommerce_external  外部电商数据
  b2b                 B2B 业务域
  ecommerce           电商域
  ai_external_data    AI 外部数据
  finance             财务/账单域
  camera              摄像头业务域
  bind                设备绑定域
  customer_care       客服域
  scraper_ods         爬虫 ODS 域
  common / abtest / http / redis 等基础设施类
```

如用户回"不知道选哪个"，跑一次：

```bash
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs?limit=400&fields=dest_database" \
  -H "xc-token: $NOCODB_TOKEN" \
  | jq -r '.list[].dest_database // empty' | sort | uniq -c | sort -rn
```

把结果贴给用户做选择参考。**不要给用户新造一个不存在的域名**——若现有域都不匹配，停下来引导用户走"新增数仓 schema"流程：

> 你需要的业务域（`<dest_database>`）当前不存在。请到数仓团队仓库提一个 issue，说明：业务背景 + 期望的 schema 名 + 大致表清单。等数仓团队建好 schema 后，再回来注册同步任务。
>
> 提 issue 命令（用户给出仓库路径后由 agent 调 `gitlab-issue-sop`）：
> ```bash
> glab issue create -R <data-platform-repo> \
>   --title "新增数仓 schema: <name>" \
>   --description "业务背景: ...\n期望 schema: <name>\n首批表: ...\n负责人: <email>"
> ```

**严禁**绕过这一步往不存在的 schema 写 jobs 行——同步引擎首次执行会报错，且没有上游审计。

## 完整字段定义

| 字段 | 类型 | 必填 | 说明 / 默认 |
|------|------|------|-------------|
| `source_table_name` | LongText | ✅ | 源表名。**JDBC 源**：`<table>` 或 `<schema>.<table>`；**NocoDB 源**：填 NocoDB 内部表 id（形如 `md_xxxxxxxxxx`，不是中文/英文表名）；**Athena 源**：用 `job_args.athena_sql` 提供查询，本字段填一个标识名即可 |
| `dest_database` | SingleLineText | ✅ | 目标库（Iceberg/Athena schema），如 `finance`、`customer_care`、`device`、`ecommerce_external`、`scraper_ods` |
| `dest_table` | SingleLineText | ✅ | 目标表名（Athena 兼容：全小写 + `_`）。**agent 按 partition_def 自动生成**（红线 #4）：`past day` → `ods_<basename>` 无后缀；`past hour` → `ods_<basename>_hf`（增量改 `_hi`） |
| `connection` (LinkToAnotherRecord) | FK→connections | ✅ | 源连接。POST 时通过 `nc_81dn__connections_id1` 设置 connections.Id |
| `dest_connection` (LinkToAnotherRecord) | FK→connections | ✅ | 目标连接，默认 `datalake`（Id=11）。POST 时通过 `nc_81dn__connections_id` 设置 connections.Id |
| `support` | MultiSelect | ✅ | 部署环境，逗号分隔字符串。可选 `prod` / `staging` / `dev`。**用户从 selector 菜单选**（红线 #3），常见 `"prod,staging"` 但不静默填 |
| `partition_def` | SingleSelect | ✅ | 分区粒度，`past day` 或 `past hour`，**用户从 selector 菜单选**（红线 #3） |
| `schedule` | SingleLineText | ✅ | cron。**用户输入**，参考：日级 `5 0 * * *`、时级 `5 * * * *` |
| `partition_start` | Date | — | 起始日期，**agent 默认填 `2025-07-01`**（沿用现表统一约定，不询问用户）。表示任务最早可调度日期，每次 cron 仅跑一个分区窗，不会全量回填 |
| `job_args` | JSON | ⚠️ | **agent 按 partition_def 自动选删分区模板**（红线 #5）。重复同步必须有 `iceberg_pre_sql` 删除目标分区，否则会重复写入 |
| `_sync_regions` | MultiSelect | — | 跨区域多副本同步，`eu` / `cn`，主区（US）外才需要 |
| `allocate_cpu` | SingleSelect | — | `1 Core`（默认） / `2/3/4/6/8 Core` |
| `allocate_memory` | SingleSelect | — | `4 GiB`（默认） / `6/8/12/16 GiB` |
| `timeout` | Number | — | 秒，默认 `3600` |
| `tags` | MultiSelect | — | `删表无权限` / `停用不迁移` / `表不存在` / `性能问题` / `弃用` |
| `description` | LongText | — | 业务描述 |
| `owner` | Email | — | 负责人邮箱 |
| `deps_on` | LongText | — | 上游依赖 |
| `k8s_config` | JSON | — | 执行 Pod 自定义配置 |

> **FK 列名**：`nc_81dn__connections_id1` = 源连接，`nc_81dn__connections_id` = 目标连接。这是 NocoDB 自动生成的列名，可能因表结构变更漂移；**写入前先反查 jobs 表 id 再用 Meta API 校验一次**（不要把 jobs 表 id 写死在脚本里）：
>
> ```bash
> # 1) 反查 data_sync 项目的 jobs 表内部 id（id 会随表重建漂移）
> JOBS_TABLE_ID=$(curl -s "https://nocodb.addx.live/api/v1/db/meta/projects/" -H "xc-token: $NOCODB_TOKEN" \
>   | jq -r '.list[] | select(.title=="data_sync") | .id' \
>   | xargs -I{} curl -s "https://nocodb.addx.live/api/v1/db/meta/projects/{}/tables" -H "xc-token: $NOCODB_TOKEN" \
>   | jq -r '.list[] | select(.title=="jobs") | .id')
> # 2) 用 jobs_table_id 取 schema 校验 FK 列名仍存在
> curl -s "https://nocodb.addx.live/api/v1/db/meta/tables/$JOBS_TABLE_ID" -H "xc-token: $NOCODB_TOKEN" \
>   | jq -r '.columns[] | select(.uidt=="ForeignKey") | .column_name'
> ```

## 只读映射查询（双向）

回答"业务库表在数仓叫什么 / 数仓完整表名是什么"或"某数仓表是从哪个业务库源表来的"。这是**纯只读 GET `jobs` 表**——不写、不改、不删任何行；查询一律带 `limit`。映射就藏在每条 jobs 记录里：

```
source_table_name + connection   ──同步任务──>   dest_database.dest_table
（业务库源表 + 源连接）                            （数仓库.表 = concat_dest_schema_table_name）
```

源类型判断（结合 `source_table_name` + 源 `connection`）：

- 以 `md_` 开头 = **NocoDB 源**（值是内部表 id，不可读，需反解 title）。
- 否则看源 `connection`：**JDBC 源**（MySQL 等业务库）`source_table_name` 是 `<schema>.<table>` 或 `<table>`，本身就是业务库表名；**Athena 源** `source_table_name` 只是个任务标识名（真实查询写在该 jobs 行的 `job_args.athena_sql` 里，不对应某张"原表"），**别把它当业务库表名给用户**。

### 正向：业务库表 → 数仓全名

输入用户给的业务库表名（如 `bills` / `mydb.bills`）：

1. **JDBC 源**：直接按表名模糊查 `source_table_name`。
   ```bash
   curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs?where=(source_table_name,like,%25bills%25)&limit=20&fields=source_table_name,dest_database,dest_table,concat_dest_schema_table_name" \
     -H "xc-token: $NOCODB_TOKEN" \
     | jq -r '.list[] | "\(.source_table_name)\t->\t\(.concat_dest_schema_table_name)"'
   ```
2. **NocoDB 源**：先用 Meta API 把"项目 + 表 title"译成内部 id `md_xxxxxxxxxx`，再按精确值查。`project_id` 不要硬编码（会因表重建漂移），先列项目取得：
   ```bash
   # a) 先列项目，按项目名取 project_id
   PROJECT_ID=$(curl -s "https://nocodb.addx.live/api/v1/db/meta/projects/" \
     -H "xc-token: $NOCODB_TOKEN" | jq -r '.list[] | select(.title=="<项目名>") | .id')
   # b) 在该项目里把表 title 译成 md_xxx
   curl -s "https://nocodb.addx.live/api/v1/db/meta/projects/$PROJECT_ID/tables" \
     -H "xc-token: $NOCODB_TOKEN" \
     | jq -r '.list[] | select(.title=="<表title>") | .id'
   # c) 用 md_xxx 精确查 jobs
   curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs?where=(source_table_name,eq,md_xxxxxxxxxx)&limit=10" \
     -H "xc-token: $NOCODB_TOKEN"
   ```

输出 `dest_database.dest_table`（直接读 `concat_dest_schema_table_name` formula 字段最省事）。告诉用户：在 Athena / Superset 用这个全名查。

### 反向：数仓表 → 业务库源头

输入用户给的数仓表名（`ods_finance_bills` 或 `finance.ods_finance_bills`）：

```bash
# 只给表名
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs?where=(dest_table,eq,ods_finance_bills)&limit=10" \
  -H "xc-token: $NOCODB_TOKEN"
# 带库名更精确（避免不同库重名）
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs?where=(dest_table,eq,ods_finance_bills)~and(dest_database,eq,finance)&limit=10" \
  -H "xc-token: $NOCODB_TOKEN"
```

从结果读 `source_table_name` + 源 `connection`：

- `source_table_name` 以 `md_` 开头 → NocoDB 源，用 Meta API 反解可读 title（列项目 → 逐项目查 tables → 匹配 `id==md_xxx`）：
  ```bash
  MD=md_xxxxxxxxxx   # 反向查到的 source_table_name
  for pid in $(curl -s "https://nocodb.addx.live/api/v1/db/meta/projects/" -H "xc-token: $NOCODB_TOKEN" | jq -r '.list[].id'); do
    curl -s "https://nocodb.addx.live/api/v1/db/meta/projects/$pid/tables" -H "xc-token: $NOCODB_TOKEN" \
      | jq -r --arg md "$MD" '.list[] | select(.id==$md) | "\($md)\t\(.title)"'
  done
  ```
- 否则看源 `connection`：**JDBC 源** → `source_table_name` 就是业务库源表名（`<schema>.<table>`），直接给用户；**Athena 源** → `source_table_name` 只是任务标识名，真实来源在该行的 `job_args.athena_sql`，把 `job_args` 给用户，别当成某张原表。

### connection（源连接）解析为业务库名

`connection` / `dest_connection` 是 LinkToAnotherRecord 外键，jobs 行响应里它**未必展开成含 `name` 的对象**（常只给连接 Id，或带 `nc_` 前缀的包装）。最稳的办法是查一次 `connections` 表建 Id→name 映射再回填（响应里恰好已展开出 `name` 时可直接用）：

```bash
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/connections?limit=200&fields=Id,name" \
  -H "xc-token: $NOCODB_TOKEN" \
  | jq -r '.list[] | "\(.Id)\t\(.name)"'
```

用 Id→name 映射把 jobs 行里的源连接翻成业务库名（如 `customer-care` / `nocodb` / `athena`），让用户确认是哪个业务库。

### 边界与红线

- **命中 0 条**：该表尚未配置入仓同步——如实告诉用户"数仓暂无此表的同步任务"，**不要编造表名**。可建议走下文「工作流：注册一个 NocoDB → datalake 同步任务」补建。
- **命中多条**：同名表跨多个业务库（不同 `connection`）或一源多目标——把每条的 `connection` + `concat_dest_schema_table_name` 列出来让用户挑，不要默认取第一条。
- **只读铁律**：本节只允许 GET。**绝不** POST / PATCH / DELETE `jobs`；要新增 / 停用同步任务走对应章节。

## 命名约定（dest_database / dest_table）

dest_database 必须在已有 schema 中选，**不允许凭空造新域**。完整推荐清单见下文"dest_database 业务域参考"小节，选其中一项展示给用户。

dest_table 由 agent 按规则自动生成（用户在最终确认表 改名）：

| `partition_def` | 自动生成模板 | 示例 |
|------|------|------|
| `past day`（日级） | `ods_<basename>`（无后缀） | `ods_user_profile`、`ods_finance_bills` |
| `past hour`（时级） | `ods_<basename>_hf`（默认全量；增量同步才用 `_hi`） | `ods_smart_popup_ticket_hf`、`ods_event_track_hi` |

basename 取源表的业务可读名：NocoDB 源用 md_xxx 对应的表 title（小写化、非字母数字下划线换成 `_`）；JDBC/Athena 源去掉 schema 前缀。结果必须符合 Athena/Hive 命名（regex `^[a-z_][a-z0-9_]*$`，长度 ≤64，不与 SQL 保留字冲突）；不合规直接报错让用户给合规 basename，不要自动 sanitize。占用检查通过 Step 5 的 NocoDB 去重 curl 完成（按 `(dest_database, dest_table)` 查 jobs 表），不需要 Athena 侧 dry-run。

## job_args 常用模板

### 1. 标准 dt 分区，按天重跑（最常见，保证幂等）

```json
{
  "iceberg_pre_sql": [
    "delete from {dest_table} where dt between date('{dt_start}') and date('{dt_end}')"
  ]
}
```

引擎会先删本次分区窗的数据再写入，因此重复触发不会产生重复行。

### 2. 按小时重跑

```json
{
  "iceberg_pre_sql": [
    "delete from {dest_table} where dt between date_parse('{dt_start}','%Y-%m-%d-%H') and date_parse('{dt_end}','%Y-%m-%d-%H')"
  ]
}
```

### 3. JDBC 源额外属性（如 MySQL tinyint(1) → Boolean 问题）

```json
{
  "iceberg_pre_sql": ["delete from {dest_table} where dt between date('{dt_start}') and date('{dt_end}')"],
  "jdbc_properties": {"tinyInt1isBit": false}
}
```

### 4. Athena 源 + Redis 目标（取最新分区，写 KV）

```json
{
  "expire": 97200,
  "athena_sql": "select id,user_id,station from device.ads_solar_user_profile_df where dt = (select max(dt) from device.ads_solar_user_profile_df)",
  "transform_sql": "select concat(user_id, ':device_health') as __redis_key,* from {source_table_name}",
  "redis_key_field": "__redis_key",
  "read_limit_rows_per_second": 2000
}
```

模板变量（同步引擎注入）：`{dest_table}`、`{source_table_name}`、`{dt_start}`、`{dt_end}`。

## 工作流：注册一个 NocoDB → datalake 同步任务

> 整个流程严格按"逐字段问用户 → 全部凑齐再展示清单 → 用户最终确认 → POST"四段。**不允许任何 DELETE 步骤**；查重发现已存在任务时，要么 PATCH（让用户决定改哪些字段），要么放弃，绝不要 DELETE 重建。

### Step 0：把"选择器字段菜单"完整发给用户

按上文"选择器字段菜单"贴出列表，让用户在一条消息里把 `partition_def` / `support` 选好。`allocate_cpu` / `allocate_memory` / `_sync_regions` / `timeout` / `partition_start` / `tags` agent 直接用默认值，**不要问用户**。

### Step 1：查源连接 Id

```bash
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/connections?where=(name,eq,nocodb)&limit=1" \
  -H "xc-token: $NOCODB_TOKEN"
# 取 list[0].Id  → 假设为 2
```

常用：

| 源类型 | name | Id |
|--------|------|----|
| NocoDB（默认） | `nocodb` | 2 |
| 数据湖（目标） | `datalake` | 11 |
| 数据湖 staging（目标） | `datalake-staging` | 26 |
| Athena | `athena` | 61 |

非默认连接每次查一次，不要写死。

### Step 2：查 NocoDB 源表的内部 Id（仅当 connection=nocodb 时）

源表在 NocoDB 的"内部表 id"用于 `source_table_name`，不是 UI 看到的 `title`。

```bash
# 列出某项目下所有表，从中找到目标表
curl -s "https://nocodb.addx.live/api/v1/db/meta/projects/{project_id}/tables" \
  -H "xc-token: $NOCODB_TOKEN" \
  | jq -r '.list[] | "\(.id)\t\(.title)"'
# 复制目标表的 id（形如 md_xxxxxxxxxx）→ 填到 source_table_name
```

### Step 3：补问 Step 0 没收到的字段（不重复提问）

> **Step 3 与 Step 0 的关系**：Step 0 是"一次性 selector 菜单"，只问 `partition_def` / `support`；Step 3 是"补漏 + 串接 Step 1/2 的 lookup 结果"，只问 Step 0 没收到的字段（即非 selector 的用户必填项）。**Step 0 已回答的字段，Step 3 绝不重复问**——直接拿 Step 0 的答案用即可。

按下面顺序补问用户，每项都用一个明确问题、给参考样例但不要替用户填：

1. **source_table_name**（必问）：源表标识。"源表是哪一个？JDBC 给 `<table>` 或 `<schema>.<table>`；NocoDB 给 `md_xxxxxxxxxx` 内部 id（不会的话告诉我项目+表 title，我帮你查）"。
2. **connection**（必问）：源连接的 `name`（如 `nocodb` / `customer-care` / `athena`）。Agent 用 Step 1 查到的 Id，不要让用户记 Id。
3. **dest_database**（必问）：目标库。把"dest_database 业务域参考"那张表贴给用户挑。用户回"不知道"或"看看现在都有哪些"时，跑参考小节里的 curl 把当前已用域列表贴给他参考；不要凭空造新域名。
4. **dest_connection**（必问）：目标连接 name（默认建议 `datalake`，但仍要用户确认）。
5. **schedule**（必问）：cron 字符串。"调度 cron 是？参考：日级 `5 0 * * *`、时级 `5 * * * *`"。

**已在 Step 0 收到、Step 3 不再重复问**：

- `partition_def` ← Step 0 selector 答案直接用
- `support` ← Step 0 selector 答案直接用

**Agent 自动生成、不向用户提问**（在 Step 4 最终确认表中展示让用户改名/调整）：

- `dest_table` ← 由 source basename + `partition_def` 推导（红线 #4）
- `job_args` ← 由 `partition_def` 选 day/hour 删分区模板（红线 #5）

**可选字段、不主动追问**（用户没给就置 null）：`description` / `owner`。

**默认值字段、不询问用户**：`allocate_cpu=1 Core` / `allocate_memory=4 GiB` / `_sync_regions=空` / `timeout=3600` / `partition_start=2025-07-01` / `tags=空`。

### Step 4：用表格列出全部配置，请用户最终确认（强制）

写入 `data_sync.jobs` 是生产配置变更。**所有配置（用户给的 + agent 自动生成的 + 默认值）必须以表格形式列出**，等用户显式回 "确认" / "修改 X 为 Y" 之后才能 POST。表格分三段：用户输入字段、agent 自动生成字段、默认字段——让用户一眼看清哪些是它选的、哪些是规则推出来的、哪些是默认。

示例：

```
准备在 data_sync.jobs 注册同步任务，请确认以下配置：

【用户输入字段】
| 字段              | 值                                  |
|-------------------|-------------------------------------|
| source_table_name | md_y4etjjsofdm9ta（AmazonApi.bills）|
| connection        | nocodb (Id=2)                       |
| dest_database     | finance                             |
| dest_connection   | datalake (Id=11)                    |
| partition_def     | past day                            |
| schedule          | 5 0 * * *                           |
| support           | prod,staging                        |

【Agent 自动生成（基于上面输入推导，可改名/调整）】
| 字段       | 值                                  | 推导依据 |
|------------|-------------------------------------|---------|
| dest_table | ods_finance_bills                   | past day → ods_<basename>，无后缀 |
| job_args   | {"iceberg_pre_sql": ["delete from {dest_table} where dt between date('{dt_start}') and date('{dt_end}')"]} | past day → 日级删分区模板 |

【默认值（不可改，除非有特殊原因）】
| 字段             | 值        |
|------------------|-----------|
| partition_start  | 2025-07-01|
| allocate_cpu     | 1 Core    |
| allocate_memory  | 4 GiB     |
| _sync_regions    | (空)      |
| timeout          | 3600      |
| tags             | (空)      |

请回复"确认"以创建，或"修改 <字段> 为 <值>"调整。
```

用户回复后：

- "确认" → 进入 Step 5 去重检查、Step 6 POST。
- "修改 X 为 Y" → 改字段重新出表格再次确认（**任何修改都要重新让用户确认整张表**，不要只口头说"改好了"）。

### Step 5：去重检查（避免重复注册）

```bash
# 按 dest_database + dest_table 查重
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs?where=(dest_database,eq,finance)~and(dest_table,eq,ods_finance_bills)&limit=5" \
  -H "xc-token: $NOCODB_TOKEN"
```

若已存在：跟用户确认是改用 PATCH 更新还是放弃。**绝不要 DELETE 旧行再重建**——会丢失审计与历史。

### Step 6：POST 创建

```bash
curl -s -X POST "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs" \
  -H "xc-token: $NOCODB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "source_table_name": "md_y4etjjsofdm9ta",
    "dest_database": "finance",
    "dest_table": "ods_finance_bills",
    "support": "prod,staging",
    "partition_def": "past day",
    "schedule": "5 0 * * *",
    "partition_start": "2025-07-01",
    "job_args": {"iceberg_pre_sql": ["delete from {dest_table} where dt between date('\''{dt_start}'\'') and date('\''{dt_end}'\'')"]},
    "nc_81dn__connections_id1": 2,
    "nc_81dn__connections_id": 11,
    "allocate_cpu": "1 Core",
    "allocate_memory": "4 GiB",
    "timeout": 3600
  }'
```

返回体含 `Id`、`concat_dest_schema_table_name`（formula，应等于 `<dest_database>.<dest_table>`）。

### Step 7：验证 + 通知用户

1. 读回该行确认 `connection` / `dest_connection` 链接正确解析（不为 `null`）：

   ```bash
   curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs/<新Id>" \
     -H "xc-token: $NOCODB_TOKEN"
   ```

2. 告知用户：

   > 同步任务已注册，下一次 cron 触发（`<schedule>`）会执行。约 N 分钟后可在 Athena/Superset 用 `<dest_database>.<dest_table>` 查询。如需立即触发，请在数仓平台手动 trigger 一次。

## 更新已存在任务

```bash
# 更新 schedule 或 job_args
curl -s -X PATCH "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs/<Id>" \
  -H "xc-token: $NOCODB_TOKEN" -H "Content-Type: application/json" \
  -d '{"schedule":"30 1 * * *","support":"prod"}'
```

不要直接 PATCH 改 `dest_database` / `dest_table`：底层 Iceberg 表已建好，改名会导致旧表数据孤立。需迁移时新建一行任务、回填后再把旧行打 `tags=弃用`。

## 停用旧任务 SOP

> **绝不 DELETE 行**——会丢失任务历史与审计。停用走 PATCH 双写：先停止调度，再标记审计。

| 步骤 | 操作 | 作用 |
|------|------|------|
| 1 | `PATCH support=""`（空字符串，所有环境都不部署） | 立刻让调度器跳过这条任务，cron 不再触发 |
| 2 | `PATCH tags="弃用"` | 给后续 review 留下审计标记，区分"暂停中"和"永久弃用" |
| 3 | `PATCH description` 追加停用原因 + 日期 | 留痕，方便 6 个月后回看 |

标准命令（按 jobs.Id 操作，先 GET 拿 Id，再 PATCH）：

```bash
JOB_ID=<待停用任务的 Id>
curl -s -X PATCH "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs/${JOB_ID}" \
  -H "xc-token: $NOCODB_TOKEN" -H "Content-Type: application/json" \
  -d '{
    "support": "",
    "tags": "弃用",
    "description": "停用：<原因>，<YYYY-MM-DD>，by <email>"
  }'
```

注意：

- `support=""` 是必须的——只打 `tags=弃用` 但 `support` 仍含 `prod/staging`，调度器还会照常跑。
- 不要 PATCH `schedule` 为非法 cron 来"间接停用"——会刷错误日志。
- 真正要清理（数仓表也想下线）时：先按上述停用 → 通知数仓团队归档 dest 表 → **由数仓团队评估后再决定是否物理删行**。本 skill 范围内永不 DELETE 行。

## 最小回归校验清单（每次改本文档后跑一遍）

文档增量很容易在 dest_table 后缀、FK 列名、jobs 表 id 这三处漂移；新增/调整规则后用以下命令快速自检：

```bash
# 1) dest_table 后缀规则一致性：日级口径必须统一为"无后缀"，禁止出现"后缀不强制"
grep -nE "past day.*(后缀不强制|无后缀)" \
  skills/nocodb/SKILL.md skills/nocodb/references/data-sync-jobs.md
# 期望：仅出现"无后缀"，无"后缀不强制"

# 2) jobs 表 id 不得硬编码（只允许在文末"参考"区做样例标注）
grep -nE "md_4prd5u3stmz9ah" \
  skills/nocodb/SKILL.md skills/nocodb/references/data-sync-jobs.md
# 期望：仅红线 #9 + 参考区有提及；其它工作流步骤一律用 <jobs_table_id> 占位 + 反查命令

# 3) 文首红线编号 1–9 各出现一次（文末不应再有"安全红线"小节）
awk '/^## 红线（先看！/,/^## 项目和表/' skills/nocodb/references/data-sync-jobs.md \
  | grep -E "^[0-9]+\." | awk -F. '{print $1}' | sort | uniq -c
# 期望：1–9 各 1 次；文末"安全红线"小节应已并入文首
grep -c "^## 安全红线" skills/nocodb/references/data-sync-jobs.md
# 期望：0（文末安全红线已删除，避免与文首红线漂移）

# 4) 跨文档锚点齐全（SKILL.md 引用的 references 小节都存在）
for anchor in "选择器字段菜单" "dest_database 业务域参考" "停用旧任务 SOP" "Step 4" "只读映射查询"; do
  grep -q "$anchor" skills/nocodb/references/data-sync-jobs.md \
    && echo "✅ $anchor" || echo "🔴 missing: $anchor"
done

# 5) 必填字段口径同步：SKILL.md 与 references 都说 7 项必填
grep -nE "这 ?7 ?项" skills/nocodb/SKILL.md skills/nocodb/references/data-sync-jobs.md
# 期望：SKILL.md 红线 #2 + references 红线 #2 + references 文末安全红线 #2 共 3 处一致
```

任何一项不符即文档已漂移，修复到一致再 commit。

## 参考

- 表元数据查询路径（agent 必须先反查 jobs_table_id，不要硬编码）：`GET /api/v1/db/meta/tables/<jobs_table_id>`，反查命令见上文"FK 列名"callout
- 现有任务样例：`GET /api/v1/db/data/v1/data_sync/jobs?limit=10&sort=-Id`
- 连接清单：`GET /api/v1/db/data/v1/data_sync/connections?limit=200`
