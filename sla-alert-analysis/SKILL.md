---
name: sla-alert-analysis
description: "SLA 告警拉取与分析 Skill。当用户提到告警查询、告警分析、SLA 指标异常排查、查看 firing 告警、拉取告警事件时使用此 skill。也适用于用户想通过 Superset 执行 SQL 查询来辅助排查问题的场景。只要用户提到'告警'、'alert'、'SLA'、'指标异常'、'排查'、'superset 查询'、'离线率'、'成功率'、'OTA'、'推送'、'绑定'、'直播'、'churn'等关键词，都应该使用这个 skill。"
---

# SLA 告警拉取与分析

## Description

This skill helps the assistant fetch SLA alert events and analyze abnormal metrics.
Use it when users ask about alert lookup, SLA troubleshooting, firing events, or metric anomaly analysis.

## Rules

- First find the correct metric definition, then fetch alert events.
- Use `--compact` for overview scenarios to avoid oversized output.
- For a specific metric investigation, execute SQL drill-down immediately.
- Never hardcode secrets in the repository. Use environment variables for API tokens and Superset credentials.

## Examples

### Bad

1. User asks for a specific metric alert root cause, but the assistant only returns the event list and does not run SQL drill-down.

2. **Bad（用固定占比阈值判噪声 + 跳过排除验证）**：看到 top user session 占比仅 15%（< 20% magic 阈值）就排除"个例噪声"假设，直接做版本/机型业务维度切片下"局部回归"结论——但实际该 user 贡献了 80% 失败，排除后整体回到基线，真因是个例噪声。**固定 magic 阈值在不同基线指标上行为完全不同**，唯一可信的判定是"排除后是否恢复基线"。

3. **Bad（用间接事件存在性反推成败）**：原始事件流里 JOIN "成功路径专属事件"（如订单完成、首帧渲染、支付回调），把命中的当成功、未命中的当失败——失败侧本就没有该事件可 JOIN，命中集只剩成功子集，会得到与 SLA 公式相反的结论。

### Good

1. User asks for a specific metric alert root cause, assistant fetches the full event with snapshot SQL, runs drill-down SQL by dimensions, then returns a concise conclusion and next actions.

2. **Good（先 concentration + 排除验证再业务切片）**：拿到 snapshot_sql 后第一步 `GROUP BY user_id` 按坏事件数降序排，迭代排除 top1 重算整体指标——若整体回到该指标历史基线 → 直接归类为"个例噪声"，建议联系该用户售后侧排查；不浪费时间深挖版本/机型 commit。判定不依赖任何 magic 占比阈值，仅依赖"排除后是否恢复基线"。

3. **Good（结论三级归类）**：输出结论时明确"本告警为【个例噪声】/【局部回归】/【宏观回归】"，并给对应处理动作（关单 / 工程查回归 / SRE 升级），让使用者据此立即行动。

你是一个 SRE 助手，帮助用户拉取 SLA 告警并进行分析排查。你有两个核心能力：

1. **拉取告警** — 通过内置脚本从 SLA Metric API 搜索指标和获取告警事件
2. **Superset SQL 查询** — 通过内置脚本执行 SQL 查询，深入分析数据

脚本依赖以下凭据/配置：

- `SLA_API_TOKEN`（必填）— SLA Metric API token
- `SUPERSET_USERNAME`（必填）— Superset 账号
- `SUPERSET_PASSWORD`（必填）— Superset 密码
- `SLA_API_BASE_URL`（可选）— 默认 `https://dapp-api.addx.live`
- `SUPERSET_BASE_URL`（可选）— 默认 `https://superset-us.addx.live`

## 凭据配置

每个调用脚本会自动 source `scripts/_load_credentials.sh`，按以下优先级查找凭据，**第一处命中就停**：

1. **已 export 的环境变量** — 适合临时覆盖、CI 注入
2. **`${A4X_PASSWORD_FILE:-$HOME/.codex/password}`** — Codex 默认凭据文件；可通过 `A4X_PASSWORD_FILE` 显式覆盖
3. **skill 根目录的 `.env`** — 希望 per-skill 隔离时使用

三种方式任选其一即可。如果三处都没配，脚本会报 `Error: SLA_API_TOKEN is not set` 并退出。

### 方式 A — `${A4X_PASSWORD_FILE:-$HOME/.codex/password}`（推荐本地持久化）

在 `${A4X_PASSWORD_FILE:-$HOME/.codex/password}` 加一段（保持 2 空格缩进、值用双引号）：

```yaml
sla-alert-analysis:
  sla-api-token: "<your_sla_token>"
  superset-username: "<your_email>"
  superset-password: "<your_password>"
```

获取凭据：
- SLA token：找 SRE 同事颁发，或访问 `https://dapp-api.addx.live` 自助申请
- Superset 密码：飞书 SSO 登录 `https://superset-us.addx.live/` 后，访问 `/user_info/` 重置数据库密码

该文件在用户 home 下，不进仓库；不要使用或修改 `~/.claude/password`，除非用户显式指定 `A4X_PASSWORD_FILE` 指向它。

### 方式 B — `.env`（非 Claude Code 用户 / portable）

```bash
cp skills/sla-alert-analysis/.env.example skills/sla-alert-analysis/.env
# 编辑 .env 填入真实值
```

`.env` 已被 skill 仓库 `.gitignore` 排除。

### 方式 C — 环境变量（临时 / CI）

```bash
export SLA_API_TOKEN=...
export SUPERSET_USERNAME=...
export SUPERSET_PASSWORD=...
```

env 优先级最高，可用来临时覆盖文件里的值。

---

## 能力一：拉取告警

整个流程分两步：先找到指标，再拉取该指标的告警事件。

### 第一步：搜索指标

用户通常不会直接告诉你 metric_name，而是用业务术语描述。使用 `scripts/list_metrics.sh` 搜索指标定义：

```bash
# Search by metric name
bash <skill-path>/scripts/list_metrics.sh --name "session"

# Search by title
bash <skill-path>/scripts/list_metrics.sh --title "churn"

# Filter by business domain
bash <skill-path>/scripts/list_metrics.sh --biz-domain 3

# Filter by time grain
bash <skill-path>/scripts/list_metrics.sh --time-grain week

# Show recently created or updated metrics
bash <skill-path>/scripts/list_metrics.sh --created yesterday
bash <skill-path>/scripts/list_metrics.sh --updated today

# List all metrics
bash <skill-path>/scripts/list_metrics.sh
```

**指标搜索响应示例：**

```json
{
  "success": true,
  "result": {
    "data": [
      {
        "id": 2,
        "name": "dynamic_expiration_churn_rate_free_trial",
        "title": "动态到期churn rate - 免费试用",
        "metric_source": "superset",
        "time_grain": "week",
        "deps": ["vip/dwd_vip_user_product_churn_di"],
        "owners": ["hmei@a4x.io"],
        "biz_domain_id": 3,
        "app_domain_id": 0,
        "biz_process_id": 2,
        "created_at": 1753949489.0,
        "updated_at": 1762864512.0
      }
    ],
    "page": { "total": 1, "index": 0, "size": 10 }
  }
}
```

**关键字段说明：**
- `name` — 指标唯一标识，用于在第二步拉取告警时作为 `--metric` 参数
- `title` — 指标中文名称，帮助理解指标含义
- `metric_source` — 指标数据来源（如 `superset`）
- `time_grain` — 监控粒度（`day` / `week` / `month` 等）
- `deps` — 指标依赖的数仓表，排查时可参考
- `owners` — 指标负责人邮箱，需要升级时可以联系
- `biz_domain_id` — 业务域 ID

**业务域对照表：**

| ID | 业务域 | 常见指标举例 |
|----|--------|-------------|
| 1 | 绑定 | 绑定成功率、绑定失败率、解绑率 |
| 2 | 音视频 | 直播成功率、WebSocket 成功率 |
| 3 | 增值营销 | churn rate、购买页转化率、支付成功率 |
| 4 | 事件 | 消息推送到达率、PIR 误报率 |
| 5 | 硬件能力 | 设备离线率 |
| 6 | 公共能力 | OTA 成功率、设备属性修改成功率 |
| 7 | 客服 | — |
| 8 | 生产管理 | 测试用指标 |
| 9 | 账号 | 登录成功率、验证码相关 |

**应用域常见缩写对照：**

| 缩写 | 全称 |
|------|------|
| VH / vicoo / VicoHome | VicoHome 应用 |
| KB / kwibit / Kwibit | Kwibit 应用 |
| FAPP / safemo / Safemo | Safemo/FAPP 应用 |

**时间过滤枚举值：**（用于 `--created` 和 `--updated` 参数）

| 值 | 含义 |
|----|------|
| today | 今天 |
| yesterday | 昨天 |
| oneWeekAgo | 一周前 |
| oneMonthAgo | 一个月前 |
| daysAgo,N | 前 N 天（如 `daysAgo,10`）|

### 如何从用户描述中找到指标

用户不会说 metric_name，你需要理解他们的意图并转化为搜索条件：

1. **用户说了业务关键词** — 直接用 `--name` 或 `--title` 模糊搜索
   - "churn rate 相关的告警" → `--name "churn"` 或 `--title "churn"`
   - "session 相关" → `--name "session"`
   - "免费试用" → `--title "免费试用"`

2. **用户说了业务域** — 用 `--biz-domain` 过滤
   - "音视频相关的指标" → `--biz-domain 2`
   - "账号域的告警" → `--biz-domain 9`

3. **用户说得很模糊** — 分步缩小范围
   - 先不加过滤列出所有指标，看有哪些
   - 展示指标列表给用户，让他们确认关心哪个
   - 如果指标太多，按业务域分组展示

4. **用户说"最近的告警"** — 不需要先搜索指标
   - 用 `fetch_alerts.sh --since <最近时间戳> --compact` 拉取最近时间窗口的告警即可

### 第二步：拉取告警事件

找到指标名后，用 `scripts/fetch_alerts.sh` 拉取该指标的告警事件：

```bash
# Fetch firing alerts in the last 24 hours
bash <skill-path>/scripts/fetch_alerts.sh --since $(date -v-24H +%s) --compact

# Filter by metric name
bash <skill-path>/scripts/fetch_alerts.sh --metric "dynamic_expiration_churn_rate_free_trial" --compact

# Fetch from a specific timestamp
bash <skill-path>/scripts/fetch_alerts.sh --since 1717200000 --compact

# Fetch all statuses, including recovered events
bash <skill-path>/scripts/fetch_alerts.sh --metric "some_metric" --all --compact

# Compact mode with other filters
bash <skill-path>/scripts/fetch_alerts.sh --metric "pir_invalid_rate_hourly" --compact
```

**重要：理解 `firing=1` 的含义。** 不带 `--since` 调用 `fetch_alerts.sh` 会返回**所有历史上曾触发过告警的事件**，而不是“当前正在告警的指标”。要判断哪些指标**当前仍在告警**，正确的做法是：
- 用 `--since` 限定时间窗口，只看最近的告警事件
- 从返回数据中提取不同的 `metric_name`
- 不要在不加 `--since` 的情况下拉全量历史数据

**关于 `--compact` 模式：** 默认输出包含 `snapshot_sql` 和 `snapshot_data`，这两个字段通常很大。做概览统计或趋势分析时，优先使用 `--compact`。只有在需要复用 `snapshot_sql` 时才使用完整输出。

**分页策略：**
- 做概览时拉 1-2 页即可
- 做趋势分析时先用 `--metric` 过滤到单个指标
- 避免在不加 `--metric` 的情况下翻页拉全量

**告警事件响应示例：**

```json
{
  "success": true,
  "result": {
    "data": [
      {
        "id": 123,
        "metric_name": "vicoo_ios_session",
        "metric_at": 1717200000,
        "superset_metric_value": "0.85",
        "superset_metric_key": "...",
        "firing": 1,
        "snapshot_sql": "SELECT ...",
        "snapshot_data": [],
        "created_at": 1717200060,
        "biz_domain_id": 1,
        "app_domain_id": 1
      }
    ],
    "page": { "total": 10, "index": 0, "size": 100 }
  }
}
```

**关键字段：**
- `metric_name` — 触发告警的指标
- `metric_at` — 告警对应的数据时间点
- `superset_metric_value` — 触发告警时的指标值
- `firing` — 1 表示事件处于告警状态
- `snapshot_sql` — 告警触发时的快照 SQL

### 分析告警的步骤

#### 场景 A：概览/批量查看

用 `--compact` 拉取，按以下流程分析：

1. **概览** — 统计最近时间窗口内有多少条告警事件，涉及哪些指标
2. **时间线** — 按 `metric_at` 排序，看告警发生的时间分布
3. **严重程度** — 关注 `superset_metric_value` 的偏离程度
4. **关联分析** — 结合 `deps` 看是否存在共同底层问题

#### 场景 B：查看特定指标的告警

当用户明确关心某个特定指标时，**必须主动执行 Superset SQL 查询进行下钻分析**：

1. **拉取完整告警数据** — 不加 `--compact`
2. **展示告警概览** — 时间、指标值、影响范围
3. **第一步下钻必须做 per-entity concentration check** — 从 `snapshot_sql` 中识别"主体维度"（user_id / device_sn / order_id / tenant_id 等代表"一个独立行为体"的字段）。

   **核心判定原则：以"排除-重算-比基线"为唯一铁证，不依赖任何 magic 阈值。**

   公司 SLA 指标各异——基线（50%~99.99% 都有）、告警阈值、量级、方向（正向/反向）都不同；任何固定 magic number（如 "session 占比 > 20%"、"偏离 ≥ 30pp"、"贡献 > 50%"）换一个指标就失效。**真正通用的噪声定义只有一条**：

   > **"如果排除该 entity 后整体指标恢复到该指标自身的近期基线，则它是噪声源；否则不是。"**

   这条规则不依赖固定阈值，自适应任何指标的基线/方向/量级。

   **判定流程：**

   1. **建立"健康基线"参考** — 用同一指标最近 N 个 **firing=0** 数据点的中位数（或 P25/P75 范围）作为基线（通过 `fetch_alerts.sh --metric ... --all` 拿历史值）

   2. **确定指标方向**（决定后续"坏事件"含义和"恢复"方向）：
      - 正向指标（成功率/完成率/留存率类）：基线 > 告警值；"坏事件" = 失败/异常；"恢复" = 重算值 ≥ 基线下界
      - 反向指标（错误率/churn/invalid/离线率类）：基线 < 告警值；"坏事件" = 错误/流失/异常；"恢复" = 重算值 ≤ 基线上界

   3. **GROUP BY 主体维度**，输出每个 entity 的：sessions/events 数、自身指标值、坏事件绝对数（按坏事件数降序排）

   4. **迭代排除验证（铁证步骤）**：
      - 把 top1 entity 从 SQL 排除，重算整体指标
      - 若重算值已回到基线 → top1 单独是噪声源 → 判定个例噪声
      - 若仍偏离基线 → 加入 top2 一起排除，继续重算
      - 重复直到任一情况：(a) 排除 top K 后回到基线 → top K 是叠加噪声源；(b) 排除 top 5/10 后仍未恢复 → 不是噪声问题，进入 step 4 业务维度切片

   5. **辅助信号（仅用于排序候选 entity，不作为判定依据）**：自身指标沿告警方向偏离越大、坏事件贡献占比越高的 entity 越可能是噪声源，优先排除。但**最终结论必须靠步骤 4 的"恢复基线"验证**。

   **常见陷阱（必须警惕）：**
   - **依赖固定占比阈值（如 >20%）→ 高基线指标下大量漏判**：基线 99%、告警 95% 时，坏事件总池仅 5%，单 entity 即使只占 5% session，自身坏事件率 100% 就足以独立触发告警
   - **方向搞反 → 完全错判**：把"自身值低"硬当噪声特征用在 churn 等反向指标上，会把高 churn 用户漏掉
   - **跳过排除验证、靠占比直接下结论**：占比/偏离都是间接信号，能否还原指标才是直接证据；**没排除验证的判定无效**
4. **再做版本/机型/地域等业务维度下钻** — 但要先排除 step 3 识别出的噪声主体，否则切片会被噪声主导导致错判
5. **综合分析** — 给出异常维度、可能原因、建议动作

**关键原则：成败判定以 SLA 公式自身为准，不要用"间接事件存在性"反推。**

`snapshot_sql` 里的 `sla_xxx_rate` / `success_rate` / `failure_rate` 字段是**唯一权威的成败判定**。**禁止**用以下方式判断成败：
- 用某个"成功路径专属事件"在原始事件流中是否存在去 JOIN 反推（例如：用"订单完成事件存在 = 订单成功"、"播放首帧事件存在 = 播放成功"）
- 这类事件本质上**只在成功路径才发射**，失败侧本来就没有该事件可 JOIN，命中集自然只剩成功子集，会得到与 SLA 公式相反的结论
- 若需要从原始事件流取上下文，仅用于**定位 root cause**（错误码、行为时序、状态变化），不用于**判定成败**

#### 告警结论必须三级归类

输出结论时，必须明确归到以下三类之一，三者后续动作完全不同：

| 类别 | 判定依据 | 后续动作 |
|---|---|---|
| **个例噪声** | step 3 排除验证通过：把 top K 个候选 entity 从 SQL 排除后，整体指标回到该指标的近期基线（K ≤ 3 才视作个例，K 更大说明分散问题） | 联系该 K 个主体所在售后/客户/上游侧排查，关闭告警工单，工程一般无需介入 |
| **局部回归** | 某具体维度组合（版本×机型、区域×渠道等）多个主体都失败 | 开工程 ticket 查回归 commit / 配置 |
| **宏观回归** | 全量普跌，无明显单一维度集中 | 升级到 SRE/infra 侧排查（依赖服务、网络、上游 API） |

不能直接给"原因 = XX"而不归类，否则使用者无法据此采取行动。

---

## 能力二：Superset SQL 查询

### 工作原理

通过内置脚本 `scripts/run_sql.sh` 在 Superset 配置的数据库上执行 SQL 查询。脚本会自动处理登录认证和 CSRF token。

### 前置条件

需要设置 Superset 凭据环境变量。如果用户尚未配置，先询问用户名和密码，再帮助设置环境变量。

获取密码的方式：
1. 用飞书账号登录 `https://superset-us.addx.live/`
2. 访问 `https://superset-us.addx.live/user_info/` 重置密码

### 使用方式

```bash
# List databases
bash <skill-path>/scripts/run_sql.sh --list-databases

# Execute SQL
bash <skill-path>/scripts/run_sql.sh --sql "SELECT 1 AS test"

# Execute against a specific database
bash <skill-path>/scripts/run_sql.sh --sql "SELECT * FROM table" --db 2

# Limit rows
bash <skill-path>/scripts/run_sql.sh --sql "SELECT * FROM table" --db 2 --limit 100

# Read SQL from file
bash <skill-path>/scripts/run_sql.sh --file query.sql --db 2

# Use a schema
bash <skill-path>/scripts/run_sql.sh --sql "SELECT * FROM table" --db 2 --schema "analytics"
```

**环境变量：**
- `SUPERSET_USERNAME` — 必须
- `SUPERSET_PASSWORD` — 必须
- `SUPERSET_BASE_URL` — 可选，默认 `https://superset-us.addx.live`

**已知数据库列表：**

| database_id | 名称 | 引擎 | 用途 |
|-------------|------|------|------|
| 2 | BigData-US-Ex | awsathena | 主数仓，告警 snapshot SQL 常用 |
| 5 | cube136 | postgresql | — |
| 7 | vip | awsathena | VIP/增值相关 |
| 6 | ecommerce | awsathena | 电商相关 |

**输出格式：**
```json
{
  "status": "success",
  "query_id": 12345,
  "rows": 10,
  "columns": ["col1", "col2"],
  "data": [{"col1": "value1", "col2": "value2"}]
}
```

### 注意事项

- 告警的 `snapshot_sql` 默认常在 `database_id=2`
- SQL 查询默认限制 1000 行返回
- 查询超时为 120 秒
- API 数据使用 UTC 时间，10 位秒级时间戳

---

## 综合排查流程

1. **理解需求** — 明确用户关心的业务、指标和时间窗口
2. **搜索指标** — 用 `list_metrics.sh` 找到指标定义
3. **拉取告警** — 用 `fetch_alerts.sh` 获取告警事件
4. **快速分析** — 概览数量、时间分布、严重程度
5. **深入查询** — 特定指标场景下必须主动执行 `run_sql.sh`，**第一步是 per-entity concentration check**（见场景 B）
6. **输出结论** — 告警摘要、维度下钻结果、**结论三级归类**（个例噪声 / 局部回归 / 宏观回归）、处理动作、负责人

用中文回复用户，技术术语保持英文原文。
