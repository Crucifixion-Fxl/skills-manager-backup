---
name: nocodb
description: NocoDB 数据管理平台操作。通过 REST API 对 NocoDB 表执行查询、新增、更新操作，以及通过 data_sync.jobs 注册"NocoDB → datalake (Iceberg/Athena)"周期同步任务。当需要读写 NocoDB 中的业务数据（SLA 指标、配置管理等）、为某张 NocoDB 表配置入仓同步、或查询业务库表与数仓表名的同步映射关系（某业务库表在数仓叫什么、数仓完整表名是什么、某数仓表来自哪个业务库源表）时使用。
---

# nocodb

通过 NocoDB REST API 管理业务数据表，支持查询、新增、更新操作。

## Description

NocoDB 是内部数据管理平台，存储 SLA 指标配置、业务域/应用域等配置数据。

| 区域 | 地址 |
|------|------|
| US（默认） | `https://nocodb.addx.live` |
| EU | `https://nocodb-eu.addx.live` |
| CN | `https://nocodb-cn.addx.live` |

默认 US，仅用户明确指定时切换。API 用法参见 [NocoDB 官方文档](https://docs.nocodb.com/developer-resources/rest-APIs)。

## Rules

### 认证

使用 `xc-token` Header 认证。Token 获取方式：打开 nocodb.addx.live → LDAP 登录 → 右上角头像 → **API Tokens** → 生成。

```bash
-H "xc-token: $NOCODB_TOKEN"
```

- Token 仅用于当前会话，**严禁写入文件或日志**

### 公司数据约定

**API 路径格式：** `/api/v1/db/data/v1/{project}/{table}`

**where 语法（公司常用模式）：**
- 等于：`(field,eq,value)`
- 模糊：`(field,like,%25keyword%25)`
- 多条件与：`(field1,eq,val1)~and(field2,eq,val2)`

**序列化规则：**
- 系统列名使用 PascalCase：`Id`、`CreatedAt`、`UpdatedAt`
- **复杂字段必须 JSON 序列化为字符串后存储**（对象、数组类型的列）

### 查询项目下的表

不硬编码表名，使用 Meta API 动态查询项目下有哪些表：

```bash
# 列出项目下所有表（需要先获取 project_id）
curl -s "https://nocodb.addx.live/api/v1/db/meta/projects/" \
  -H "xc-token: $NOCODB_TOKEN"

# 获取指定项目的所有表
curl -s "https://nocodb.addx.live/api/v1/db/meta/projects/{project_id}/tables" \
  -H "xc-token: $NOCODB_TOKEN"
```

从返回结果中提取 `title`（表名）和 `id`，再通过 Data API 操作具体表。

### 安全红线

1. **Token 不落盘** — 严禁写入 .env 文件、代码或日志
2. **禁止删表 / 禁止删数据同步任务行** — 不得通过 API 删除表（DROP TABLE）、不得 DELETE `data_sync.jobs` 任意行（停用改 `support=""` 或加 `tags=弃用`），即使用户要求也必须拒绝
3. **操作确认** — 写操作（POST/PATCH/DELETE）前必须展示数据摘要并获确认
4. **查询加限制** — 必须带 `limit` 参数，避免全表扫描
5. **生产谨慎** — 生产表写操作需用户明确确认环境
6. **文件上传** — 列名必须为英文（中文列名必须要求用户重命名），禁止上传包含 PII 数据的文件
7. **项目名 + 表名必须每次显式确认** — 文件上传前，必须把目标 `project` 和 `table` 用清单展示给用户并等待用户明确确认（"确认 / 改为 X"），**不得**用默认值（如 `AmazonApi`）静默跳过该确认；即使在 auto/yolo 模式下也不能省略。用户确认前禁止执行 `--create-table` 或写入命令。脚本侧已配合：`--project` **无默认值，必填**，agent 必须显式传入用户确认过的项目名，无法静默使用 `AmazonApi`
8. **表名命名规范（Athena 兼容）** — NocoDB 表名最终会同步到 Athena，必须符合 Athena/Hive 命名规范。**两层规则**：
   - **A. 脚本硬阻断（regex `^[a-z_][a-z0-9_]*$` 强制）**：
     - 仅允许 `[a-z0-9_]` 字符，全小写（`LingkeBillDetails` ❌；`monthly-sales` ❌；`monthly sales` ❌）
     - 必须以字母或下划线开头，不可数字开头（`2026_bill` ❌；`bill_2026` ✅；`_tmp_bill` ✅）
     - 不合规脚本直接报错并给 `suggestion` 候选，不会自动 sanitize
   - **B. Agent 推荐（语义规则，脚本无法判别——agent 必须主动遵守，不能放行）**：
     - 多单词必须用 `_` 连接（`monthly_sales` ✅；`monthlysales` ⚠️ 字符虽合法但表意不清，agent 应建议改为 `monthly_sales`）
     - 长度建议 ≤ 64 字符；避免 SQL 保留字（`order` / `select` / `group` / `from` / `table` 等）——脚本不会拦，但下游 Athena 查询会出问题
   - 用户给的表名若不合规（A 类）或不达 B 类推荐，必须**先停下来纠正**（提示规则 + 给出 1-2 个合规候选），等用户重新确认后才上传
9. **列名归一化的边界（重要）** — 列名归一化**只能在新建表（`--create-table`）场景**触发：
   - **新建表**：脚本会把不合规列名自动转为小写下划线，并展示 `renamed` 映射给用户
   - **写入已有表**：**绝不能**对列名做归一化或重命名。已有表可能：a) 已积累历史数据；b) 下游 Athena/Superset 已建好表结构；c) 其他系统在依赖现列名。重命名会破坏下游链路、产生孤立列、或导致 bulk insert 报错。此时脚本会自动 fetch 现表 schema 与文件 header diff，不匹配则报错；agent 应基于 diff 用 `--column-map` 做 in-flight 映射（不改文件不改表）
   - 如果用户在已有表上发现列名不规范但已有数据，需要走数据迁移流程（新建合规表 + 数据回填 + 切换下游 + 旧表归档），不能在原表上原地改名
10. **中文列名翻译（agent 主动出方案）** — 脚本不内置翻译；当检测到中文列时报错。Agent 必须根据列名语义 + 文件 sample 数据**主动生成英文翻译候选**（snake_case），把完整映射展示给用户确认后再用 `--column-map` 重试。不要把"请把中文列翻译为英文"这种空指令甩给用户
11. **列名映射的持久化与复用（关键，避免翻译漂移）** — 同一份源文件按月/按周重复导入是常见场景。为保证多次导入的列名一致：
    - 建表成功（`--create-table`）后，脚本会自动落盘 sidecar 到 `~/.cache/nocodb_column_maps/<host>__<project>__<table>.json`，内容是 `file_original_header → final_table_column` 的完整映射（success 响应里的 `column_map_saved` 字段会回传路径）
    - 后续向**同一表**导入新文件时，脚本会**自动加载**该 sidecar（`column_map_used` 字段会显示 `cache:<path>`）；也可以显式 `--column-map @<path>` 指向其他映射文件
    - 优先级：`--column-map` 显式参数 > sidecar cache > 无映射
    - **Agent 流程**：每次往已有表导入前，先检查 `~/.cache/nocodb_column_maps/<host>__<project>__<table>.json` 是否存在；存在则直接使用（无需重新让 LLM 翻译）；不存在再走 diff + 翻译候选 + 用户确认流程，并在第一次成功后让脚本生成 sidecar
    - 路径可通过 `NOCODB_COLUMN_MAP_DIR` 环境变量覆盖；多人协作场景建议提交到仓库或共享盘统一维护

### 文件上传（CSV/Excel → NocoDB）

通过上传脚本将本地 CSV/Excel 文件写入 NocoDB 表，支持新建表和写入已有表。

- 上传脚本位置：`skills/nocodb/scripts/upload_to_nocodb.py`
- 目标项目固定为 `AmazonApi`
- 需要 `NOCODB_TOKEN` 环境变量或 `--token` 参数

#### 工作流

**Step 1：预览文件**

```bash
uv run scripts/upload_to_nocodb.py --file <文件路径> --project AmazonApi --table <表名> --preview
```

输出列名、行数、样例值。脚本行为：

- **表名**：必须由用户给出合规名，若不合规脚本报错并给出 `suggestion` 候选，不会自动 sanitize（参见 Rule #8）
- **中文列名（必须翻译为英文）**：脚本检测到中文列后会报错并提示需要 `--column-map`。**Agent 的职责**：基于列语义和 sample 数据为每个中文列**主动生成英文翻译候选**，把映射展示给用户确认（"接受 / 改 X 列"），用户确认后用 `--column-map '{"原中文":"english_name", ...}'` 重新执行。**禁止**直接抛错让用户自己想英文名
- **列名归一化（仅 `--create-table` 模式触发）**：在 `--column-map` 翻译之后，脚本会再把大写/空格/连字符/点/括号等自动归一化为小写下划线，并在 preview 输出 `renamed` 字段
- **写入已有表（不带 `--create-table`）**：脚本会先 fetch 现表 schema，对文件 header 做 diff（`in_file_not_in_table` / `in_table_not_in_file`）。任何不匹配都直接报错，**绝不**自动改名。**Agent 的职责**：把 diff 展示给用户，用 `--column-map` 把文件列名映射到现表列名（in-flight rename，不改文件、不改表）后重试。如果是表多列/文件少列且需要全表重建，需走数据迁移流程，不能在原表原地改

预览时：
- `create_table=true` 把 `renamed` 列表展示给用户让其可见可改
- `create_table=false` 提醒用户文件 header 必须与现表 schema 完全一致；上传时若 diff 报错，按上一段流程处理

**Step 2：用户确认参数（强制 — 每次上传都要走，不能跳过）**

执行写入前，必须把以下清单完整展示给用户，并**等待用户显式确认**（"确认" / "改为 X"）。即使是 auto 模式或用户已经隐含同意整体任务，也必须单独就 `project` 和 `table` 拿到一次明确回复——不得用默认值静默跳过。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `project` | NocoDB 项目（脚本侧 `--project` 已**改为必填，无默认值**） | 无（必填，**必须用户每次确认**；常用 `AmazonApi` 但不得静默使用） |
| `table` | NocoDB 表名（必须符合 Athena 命名规范：全小写 + `_` 分隔） | 无（必填，**必须用户每次确认**） |
| 区域 | US / EU / CN | US（仅用户明确指定时切换） |
| 列名 | 基于 Step 1 预览，中文必须改英文 | 文件原始列名 |
| 行数 / 是否新建表 | 显示总行数和 `--create-table` 是否启用 | — |

确认话术示例：

> 准备上传到 NocoDB：项目 `AmazonApi`、表 `<table_name>`（新建）、共 N 行。**请确认项目名和表名**，或回复修改。

**Step 3：上传（用户已明确确认 project + table 之后才能执行）**

写入已有表（脚本会自动加载 `~/.cache/nocodb_column_maps/<host>__<project>__<table>.json`）：
```bash
uv run scripts/upload_to_nocodb.py --file data.csv --project AmazonApi --table my_table
```

显式指定列映射（覆盖 sidecar cache）：
```bash
uv run scripts/upload_to_nocodb.py --file data.csv --project AmazonApi --table my_table \
  --column-map '{"姓名":"name","年龄":"age"}'
# 或加载已保存的映射文件
uv run scripts/upload_to_nocodb.py --file data.csv --project AmazonApi --table my_table \
  --column-map @~/.cache/nocodb_column_maps/nocodb.addx.live__AmazonApi__my_table.json
```

创建新表并写入（建表成功后自动落盘 sidecar，下次复用）：
```bash
uv run scripts/upload_to_nocodb.py --file data.csv --project AmazonApi --table my_table --create-table
```

**Step 4：验证**

上传完成后通过 NocoDB API 查询确认：
```bash
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/AmazonApi/<table>?limit=5" -H "xc-token: $NOCODB_TOKEN"
```

**Step 5：注册数仓同步任务（自助）**

上传成功后，提醒用户：

> 表 `<table>` 已写入 NocoDB。要让数仓侧（Athena/Iceberg）能查到，需要在 `data_sync.jobs` 里注册一个同步任务。是否现在配置？

如果用户回 yes，进入下一节"数据同步到数仓 (data_sync.jobs)"。如果用户暂不需要，记下后续再配置即可。

## 数据同步到数仓 (data_sync.jobs)

为 NocoDB 表（或任何 JDBC / Athena 数据源）注册一条 `data_sync.jobs` 记录，触发周期性同步到 `datalake`（Iceberg）→ Athena 可查。**这是生产配置变更**，按下述强约束执行。完整流程、字段定义、curl 示例参见 [`references/data-sync-jobs.md`](references/data-sync-jobs.md)。

### 红线（入口必看精简版；完整 9 项 + 实施细节见 [references/data-sync-jobs.md "红线（先看！）"](references/data-sync-jobs.md)）

> 唯一红线真源在 references。本节摘列入口要点，每条标注 `→ 红线 #N` 指向 references 详细条款。文档其它任何位置不得再独立列红线，避免漂移。

1. **严禁 DELETE / DROP**（→ 红线 #1）：不得 DELETE `data_sync.jobs` 任意行、不得删源表/目标表数据。停用走 PATCH（见下方"额外入口约束"）。
2. **7 项关键字段用户确认**（→ 红线 #2）：`source_table_name` / `connection` / `dest_database` / `dest_connection` / `partition_def` / `schedule` / `support`，agent 不得猜值。其余字段用默认值不询问：`cpu=1 Core / mem=4 GiB / regions=空 / timeout=3600 / partition_start=2025-07-01 / tags=空`。
3. **Selector 必须菜单展示**（→ 红线 #3）：`partition_def` / `support` 用编号列表 `[1]...[2]...` 让用户选。完整菜单见 references "选择器字段菜单"小节。
4. **`dest_table` agent 自动生成**（→ 红线 #4）：日级 `ods_<basename>` 无后缀；时级默认 `ods_<basename>_hf`（增量改 `_hi`）。最终确认表里给用户最后改名机会。
5. **`job_args` agent 按 `partition_def` 自动选模板**（→ 红线 #5）：day/hour 删分区模板；最终确认表里给用户调整机会。
6. **`dest_database` / `dest_table` 不可改名**（→ 红线 #6）：迁移走"新建任务行 + 旧行打 tags=弃用"。
7. **`dest_database` 不允许凭空造新域**（→ 红线 #7）：现有域都不满足时引导用户提 issue 申请新增 schema（走 `gitlab-issue-sop`），严禁往不存在的 schema 写 jobs 行。
8. **NocoDB 源的 `source_table_name` 必须 `md_xxxx`**（→ 红线 #8）：不是 title。
9. **FK 列名 + jobs 表 id 漂移防御**（→ 红线 #9）：POST 前先反查 jobs_table_id 再 GET meta 校验 FK 列名，不要硬编码 jobs 表 id。

**额外入口约束**（不在 references 红线，但本 skill 必须执行）：

- **POST 前用表格让用户最终确认**：用户输入 + agent 自动生成 + 默认值 三段表格列出全部配置，等用户回"确认"才能 POST，任何修改都要重新出表格。模板见 [references/data-sync-jobs.md Step 4](references/data-sync-jobs.md)。
- **停用旧任务走 PATCH 三步 SOP**：`support=""`（停调度）+ `tags="弃用"`（审计）+ `description` 追加停用原因。SOP + 标准命令见 [references/data-sync-jobs.md "停用旧任务 SOP"](references/data-sync-jobs.md)。

### 工作流（精简版，详细见 references/data-sync-jobs.md）

1. **Step 0 — 把选择器菜单一次性发给用户**：贴出 `partition_def` / `support` 的可选项编号列表，让用户一次性选好。`allocate_cpu` / `allocate_memory` / `_sync_regions` / `timeout` / `partition_start` / `tags` 用默认值，不询问。
2. **Step 1 — 解析连接 Id**：`GET /api/v1/db/data/v1/data_sync/connections?where=(name,eq,<用户给的连接名>)&limit=1` 拿 Id，不要让用户记 Id。
3. **Step 2 — 若源是 NocoDB**：用 Meta API 把用户给的"项目+表 title"翻译成 `md_xxxxxxxxxx` 内部 id，填到 `source_table_name`。
4. **Step 3 — 逐字段问用户**（见 references 里 Step 3 列表）。
5. **Step 4 — 用表格列出最终配置等用户确认**：分三段（用户输入 / agent 自动生成 / 默认值）以表格形式展示，等用户回"确认"之后才能 POST；任何修改都要重新出表格再确认一次。模板见 references/data-sync-jobs.md Step 4。
6. **Step 5 — 去重检查**：按 `(dest_database,dest_table)` 查现有 jobs；已存在让用户决定 PATCH 还是放弃。**绝不 DELETE 旧行重建**。
7. **Step 6 — POST**：`POST /api/v1/db/data/v1/data_sync/jobs`，FK 字段用 `nc_81dn__connections_id1`（源）和 `nc_81dn__connections_id`（目标，默认 11=datalake）。POST 前**先反查 jobs 表的 NocoDB 内部 id**（用项目 `data_sync` 的 tables 列表 + filter title=jobs 拿 `md_xxx`），再 `GET /api/v1/db/meta/tables/<jobs_table_id>` 校验 FK 列名仍存在；不要硬编码 jobs 表 id（NocoDB 重建表后会漂移）。
8. **Step 7 — 验证 + 通知**：读回新行确认 `connection` / `dest_connection` 解析非 null；告知用户下一次 cron `<schedule>` 触发后即可在 Athena/Superset 用 `<dest_database>.<dest_table>` 查询。

> **更新已有任务用 PATCH**，不要 DELETE 后 POST。`dest_database` / `dest_table` 不可改名（会让旧分区数据孤立），需迁移时新建一行任务、旧行打 `tags=弃用`。

## 查询数仓映射关系（业务库表 ↔ 数仓表，只读）

回答"某业务库表在数仓叫什么 / 数仓完整表名是什么"或"某数仓表是从哪个业务库源表同步来的"——映射关系就在 `data_sync.jobs` 每条同步任务里：`source_table_name` + `connection` 是业务库源，`dest_database.dest_table` 是数仓表，formula 字段 `concat_dest_schema_table_name` 已拼好 `库.表`。这是**纯只读 GET**，但仍守查询红线：带 `limit`、**绝不**改 jobs 行。

> **没有 token，或查 `data_sync` 报无权限？** 该查询需要账号有 `data_sync` 项目访问权限。如果还没有：① 联系**数据团队同学**把你**邀请进 `data_sync` 项目**；② 加入后打开 https://nocodb.addx.live → LDAP 登录 → 右上角头像 → **API Tokens** → **自己生成**；③ 重试本查询。数据同学只负责拉你进项目，凭证始终你自己生成、自己用。

完整步骤、connection / `md_xxx` 解析与边界处理见 [`references/data-sync-jobs.md` 「只读映射查询（双向）」](references/data-sync-jobs.md)。两个方向的查询要点：

**正向（业务库表 → 数仓全名）**：JDBC 源直接按表名模糊查 `source_table_name`；NocoDB 源先用 Meta API 把表 title 译成 `md_xxxxxxxxxx` 再查。

```bash
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs?where=(source_table_name,like,%25bills%25)&limit=20" \
  -H "xc-token: $NOCODB_TOKEN" \
  | jq -r '.list[] | "\(.source_table_name)\t->\t\(.dest_database).\(.dest_table)"'
```

**反向（数仓表 → 业务库源头）**：按 `dest_table` 反查（带库名再加 `~and(dest_database,eq,...)`），拿到 `source_table_name` + 源连接；`source_table_name` 以 `md_` 开头说明是 NocoDB 源，需再用 Meta API 反解成可读 title。

```bash
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/data_sync/jobs?where=(dest_table,eq,ods_finance_bills)&limit=10" \
  -H "xc-token: $NOCODB_TOKEN"
```

命中 0 条 = 该表未配置入仓同步；命中多条 = 同名表跨多个业务库，按 `connection`（源连接名）让用户区分。

## Examples

### Bad

```bash
# 无 limit 全表查询
curl "https://nocodb.addx.live/api/v1/db/data/v1/BI/sla_metric" -H "xc-token: $TOKEN"
# ❌ 必须加 limit 参数

# Token 写入文件
echo "NOCODB_TOKEN=xxx" >> .env
# ❌ 严禁持久化 Token

# 上传：中文列名未处理直接上传
uv run scripts/upload_to_nocodb.py --file 销售数据.csv --project AmazonApi --table sales --create-table
# ❌ 必须先预览检查列名

# 用户说"确认导入"就直接上传，没有展示 project + table 清单
uv run scripts/upload_to_nocodb.py --file data.xlsx --project AmazonApi --table my_table --create-table
# ❌ 即使用户表达"确认"，仍必须先展示 project=AmazonApi、table=my_table 清单，等用户对这两个字段单独确认

# 表名不符合 Athena 规范（大写 / 连字符 / 数字开头）就直接上传
uv run scripts/upload_to_nocodb.py --file data.xlsx --project finance --table MonthlySales --create-table
uv run scripts/upload_to_nocodb.py --file data.xlsx --project finance --table monthly-sales --create-table
uv run scripts/upload_to_nocodb.py --file data.xlsx --project finance --table 2026_bill --create-table
# ❌ 必须停下，提示用户改为：monthly_sales / monthly_sales / bill_2026 后再确认
```

### Good

```bash
# 带条件和限制的安全查询
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/BI/sla_metric?where=(name,like,%25order%25)&limit=20&offset=0" \
  -H "xc-token: $NOCODB_TOKEN"

# 上传完整流程：预览 → 确认 → 上传 → 验证 → 注册数仓同步
# 1. 预览
uv run scripts/upload_to_nocodb.py --file monthly_sales.csv --project AmazonApi --table monthly_sales --preview
# 2. 确认无中文列名后上传（新表）
uv run scripts/upload_to_nocodb.py --file monthly_sales.csv --project AmazonApi --table monthly_sales --create-table
# 3. 验证
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/AmazonApi/monthly_sales?limit=5" -H "xc-token: $NOCODB_TOKEN"
# 4. 自助注册数仓同步任务（参见"数据同步到数仓 (data_sync.jobs)"小节）：
#    - 解析 source/dest 连接 Id；查 NocoDB 内部表 id；选 selector 菜单；逐字段问用户；展示清单确认；POST 创建
#    - 写入后下一次 cron 触发即可在 Athena/Superset 通过 ods_xxx 查询
```

## References

- [NocoDB 官方 API 文档](https://docs.nocodb.com/developer-resources/rest-APIs)
- [`references/data-sync-jobs.md`](references/data-sync-jobs.md) — 数仓同步任务 (`data_sync.jobs`) 完整字段定义、选择器菜单、curl 示例、job_args 模板、只读映射查询（双向）、安全红线
- 源码：`/project/dbt/utils/_nocodb/client.py`（NocoDBClient）
- 源码：`/project/dbt/utils/_nocodb/admin.py`（NocoDBAdminClient）
