## Description

通过 Troubleshooting API 查询设备的绑定状态与在线/离线状态。

- 确认绑定状态时先查 `prod-us`，未绑定再查 `prod-eu`；任一环境已绑定即输出 `已绑定`，仅当两边都未绑定时才输出 `未绑定`。
- 只对已绑定设备查询在线/离线状态。
- 处理 Excel 时按**一行一设备**展开输出，详见 [references/excel-spec.md](references/excel-spec.md)。

### 认证

使用 `troubleshooting` skill 的 `get_token.mjs` 获取 JWT token：

```bash
# SKILL_DIR 指向 troubleshooting skill Base directory（会话启动时由 Skill 工具注入）
# macOS / Linux (bash/zsh)
SKILL_DIR="<troubleshooting skill Base directory>"
node "$SKILL_DIR/references/get_token.mjs"
export TROUBLESHOOTING_TOKEN="<token>"

# Windows PowerShell
$SKILL_DIR="<troubleshooting skill Base directory>"
node "$SKILL_DIR\references\get_token.mjs"
$env:TROUBLESHOOTING_TOKEN="<token>"

# Windows CMD
set SKILL_DIR=<troubleshooting skill Base directory>
node "%SKILL_DIR%\references\get_token.mjs"
set TROUBLESHOOTING_TOKEN=<token>
```

所有请求需包含 `Authorization: Bearer $TROUBLESHOOTING_TOKEN`（Windows CMD 中引用该变量请使用 `%TROUBLESHOOTING_TOKEN%`）。

### API 信息

| 环境 | 基础 URL |
|------|----------|
| `prod-us` | `https://troubleshooting-us.addx.live` |
| `prod-eu` | `https://troubleshooting-eu.addx.live` |

- 主接口：`/api/v1/log-search/query/db`
- Swagger 文档：`https://troubleshooting-{us,eu}.addx.live/api/docs`
- 所有查询统一使用：`deidentify: true`、`encrypted: false`

### 设备标识识别

| 输入格式 | 示例 | id_type |
|---------|------|---------|
| 14/15 位 User SN | `KC3NQXFBED2247`、`AIC1JPE1S1A1003`、`HUB1234ABCD5678` | `user_sn` |
| 32 位 UID | `28d61f9dc94ce3212e23ad67a9428e95` | `device_sn` |

> 本指南只查 Prod。14 位 `GL` User SN 当前仅 Staging 后端已支持；生产后端发布 GL 校验前，不要将 GL 值发给上述 Prod 接口。

## Rules

### Rule 1：绑定状态判定

绑定状态与在线状态查询复用同一个请求结构（仅 `id_type/id_value` 按输入设备变化）：

```json
{
  "id_type": "user_sn",
  "id_value": "AICNYCEQWNS1752",
  "start_date": "2026-03-09",
  "end_date": "2026-03-16",
  "db_queries": {
    "user_device_binding": ["all"],
    "user_info": ["all"]
  },
  "es_data": {},
  "deidentify": true,
  "encrypted": false
}
```

查询 `user_device_binding`，从 `UserDeviceBindingQuery[0].factoryInfoDOS[0]` 提取 `isBind`。

执行顺序：

1. 先查 `prod-us`
2. `prod-us` 返回 `1-已绑定` → 最终 `已绑定`，跳过 `prod-eu`
3. `prod-us` 返回 `0-未绑定` → 继续查 `prod-eu`
4. `prod-eu` 返回 `1-已绑定` → 最终 `已绑定`
5. `prod-us` 和 `prod-eu` 都返回 `0-未绑定` → 最终 `未绑定`
6. 两边都查询失败 → 最终 `查询失败`

| `prod-us` | `prod-eu` | 最终绑定状态 |
|----------|-----------|-------------|
| `1-已绑定` | 无需查询 | `已绑定` |
| `0-未绑定` | `1-已绑定` | `已绑定` |
| `0-未绑定` | `0-未绑定` | `未绑定` |
| 查询失败 | 查询失败 | `查询失败` |

对外只输出 `已绑定` / `未绑定` / `查询失败`。

### Rule 2：在线/离线判定

仅对**已绑定**设备判定在线状态。绑定与在线状态复用同一请求结果，从 `UserDeviceBindingQuery[0].deviceStatuses[0]` 读取状态字段。

同时满足以下 3 个条件时判定为 `在线`，否则为 `离线`：

| 条件 | 字段 | 值 |
|------|------|----|
| 1 | `status` | `已休眠并连上websocket` |
| 2 | `reason` | `dormantStatus` 或 `mqttreceive` |
| 3 | `updateTime` | 距当前时间 < 1 小时 |

`status = 已关机` 时，可根据 `reason` 补充关机原因：

| `reason` | 说明 |
|----------|------|
| `1` | 低电量关机 |
| `2` | 按键关机 |
| `4` | 解绑关机 |
| `5` | 太阳能充电低电量关机 |

### Rule 3：Excel 批量处理

处理 Excel 时必须按**一行一设备**展开输出。同一输入行拆出的多台设备用 `输入序号` + `行内设备序号` 区分归属。

完整的列定义、输出约束和示例见 [references/excel-spec.md](references/excel-spec.md)。

核心约束摘要：
- 不允许把多个设备拼成一段文本放进单个单元格
- 不允许只输出汇总计数而不展开到设备级
- 查询失败的设备也必须保留一行结果
- Excel 读取时按**无表头**处理（即首行也作为数据参与解析）
- 对同一输入行内拆出的设备做**去重校验**（保留首次出现顺序）

### Rule 4：安全红线

- 仅执行只读查询
- 禁止修改、删除生产数据
- 查询时间范围不超过 30 天

## Examples

### Bad

**错误 1：只查了一个环境就判定未绑定**

```
查询 prod-us → isBind = 0-未绑定 → 直接输出"未绑定"
```

未查询 `prod-eu`，违反 Rule 1。实际设备可能在 EU 节点绑定。

**错误 2：Excel 输出把多台设备合并到一格**

```
第2行 | KC3NQXFBED2247: 已绑定; 28d61f9dc94ce3212e23ad67a9428e95: 未绑定
```

违反 Rule 3。必须每台设备独占一行。

**错误 3：未绑定设备还去查在线状态**

```
设备 X → 未绑定 → 继续查询 device_status → 在线状态: 查询失败
```

违反 Rule 2。未绑定设备应跳过在线查询，`在线状态` 直接留空。

### Good

**单设备查询**

```
用户：查询 AIC1JPE1S1A1003 的状态

1. 识别 15 位 SN → id_type = user_sn
2. 查询 prod-us → isBind = 1-已绑定
3. 最终绑定状态 = 已绑定（无需查 prod-eu）
4. 查询 prod-us device_status
5. status=已休眠并连上websocket, reason=dormantStatus, updateTime 在 1 小时内
6. 最终在线状态 = 在线
```

**Excel 批量查询**

输入 Excel 第 2 行含 `KC3NQXFBED2247,28d61f9dc94ce3212e23ad67a9428e95`，输出：

| 输入序号 | 行内设备序号 | 设备标识 | 绑定状态 | 在线状态 | 错误信息 |
|----------|-------------|----------|----------|----------|----------|
| 2 | 1 | KC3NQXFBED2247 | 未绑定 |  |  |
| 2 | 2 | 28d61f9dc94ce3212e23ad67a9428e95 | 已绑定 | 离线 |  |

两台设备各占一行，`输入序号` 相同标识来自同一原始行。

## 相关资源

- Excel 输出规范详情：[references/excel-spec.md](references/excel-spec.md)
- 参考脚本：[scripts/query_device_status_excel.py](scripts/query_device_status_excel.py)
- Troubleshooting Skill：`<troubleshooting skill Base directory>/SKILL.md`
