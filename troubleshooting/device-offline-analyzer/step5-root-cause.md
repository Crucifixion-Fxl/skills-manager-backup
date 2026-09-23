# STEP 5: 根因排查

> **SKILL_DIR** = `<troubleshooting skill Base directory>/device-offline-analyzer`（同 guide.md STEP 2，此文件单独加载时须确认该变量已设置）

> 进入条件：状态 3 经情况 A 决策树仍无法恢复、状态 2 触发异常离线规则。

**先决条件**：须先通过 `troubleshooting` Skill 确认设备近一周的 Backend-Log 后端日志已加载。加载失败则如实告知，不得编造查询结果，终止根因排查。

采用两阶段并行策略排查三个维度：

**阶段 1（并行）**：使用 `superpowers:dispatching-parallel-agents` Skill 同时派发两个子代理：
- 子代理 A：执行 5.1 设备维度（含 5.1.1–5.1.4），完成后输出 `step5_device_summary.md`
- 子代理 B：执行 5.3 服务器维度，完成后输出 `step5_server_summary.md`

**阶段 2（串行，等待阶段 1 完成）**：主代理读取 `step5_device_summary.md`，执行 5.2 网络维度。

**阶段 3**：综合三个维度输出 5.4 结论。

每个维度须基于实际查询数据得出结论。

### 5.1 设备维度

**5.1.1 恢复方式（POR 唤醒类型）分析**

调用 `/api/v1/log-search/query/es`（Rule 7 ES 基础结构），`fuzzy_search` = `"reportType : deviceWakeupRequest"`，保存至 `step5_por_p1.json`。分页处理见 Rule 8，覆盖所有 STEP 4.1 中 `fromState = state_connection_lost` 的时间点后停止。

提取字段：`timestamp`、`por`、`dtim`、`battery`、`charge`、`firmwareId`、`FromPowerUpToMqttSend`、`wakeupId`。

**POR 关联分析**：将每次离线恢复时间点与最近唤醒记录匹配，确定 POR 唤醒类型。POR 含义通过 `lark` MCP 从[唤醒原因POR说明](https://a4x-paas.feishu.cn/wiki/FcgtwYL82i0NuUk0OYLcilFSnnd)获取。

| 恢复时间（UTC+8）     | POR 值 | 唤醒类型         |
| -------------------- | ------ | ---------------- |
| 2026-03-18 19:00:53  | 2048   | （从飞书文档获取）|

**POR 统计**：遍历所有分页 `por` 字段，按 POR 值分组统计出现次数，辅助判断设备离线恢复的主要方式。

**5.1.2 DTIM 值分析**

从 `step5_por_p*.json` 提取 `timestamp` 和 `dtim` 字段，检查所有记录的 `dtim` 值是否一致。正常值 = 7；出现 DTIM < 7 说明设备主动降低省电策略以提高连接稳定性，记录异常时段供 5.2.1 交叉印证。

**5.1.3 电量分析**

从 `step5_por_p*.json` 提取 `timestamp`、`battery`（电量%）、`charge`（充电状态），按时间顺序排列观察趋势。

**异常判定**：连续两次唤醒 `battery` 跳变 > 10% → 标记为电池/上报异常。

**关联分析**：将每次离线（`toState = state_connection_lost`）与最近唤醒记录匹配，检查离线前电量：

- 电量 ≤ 1% 且未充电，且恢复 POR = 2048 → **低电关机离线**（STEP 5.4 建议检查供电：充电器规格、太阳能板、电池寿命）
- 电量 > 10% → 排除电量因素

**5.1.4 设备埋点分析**

`uid` 字段取值：`device_sn`（`encrypted=false`）直接用 32 位 UID；`device_sn`（`encrypted=true`）直接用脱敏 ID；`user_sn` 从 `step1_db.json` 中 `UserDeviceBindingQuery[0].serialNumber` 提取脱敏 ID。

调用 `/api/v1/chart/data`（Rule 7 通用请求头），请求体：

```json
{
  "id": 9,
  "filters": [
    { "col": "event_time", "op": "TEMPORAL_RANGE", "value": "<start ISO> : <end ISO>" },
    { "col": "event_name", "op": "IN", "value": ["device_shutdown","soc_cpu_high","soc_mem_high","soc_res_monitor"] },
    { "col": "uid", "op": "==", "value": "<uid>" }
  ],
  "columns": ["event_time", "event_name", "unstruct_event_data"],
  "sort_col": "event_time",
  "sort_asc": false,
  "mode": "query"
}
```

保存至 `step5_tracking.json`，调用脚本解析（`event_time` 转为北京时间）：

```bash
node "$SKILL_DIR/scripts/parse_data_tracking.js" ./step5_tracking.json
```

脚本输出空表时，记录"无异常埋点"，跳过关联分析。埋点含义通过 `lark` MCP 从[设备其他小功能埋点](https://a4x-paas.feishu.cn/wiki/NZRCwppVsinOfOkks8KcKamjnkh)获取。

**关联分析**（离线时间点前后 **30s** 窗口内的埋点）：

- `device_shutdown`：结合 5.1.3 和 POR 交叉确认；电量正常时重点看 `unstruct_event_data` 中的关机原因字段
- `soc_cpu_high`/`soc_mem_high`：离线前密集出现 → 可能资源耗尽导致连接中断
- `soc_res_monitor`：提供 CPU/内存趋势，辅助判断持续性资源压力

**5.1 子代理产出物**

完成 5.1.1–5.1.4 后，将以下结构写入 `step5_device_summary.md`，供阶段 2 的 5.2.1 交叉使用：

```
## DTIM 异常时段
（列出 dtim < 7 的所有时间点，格式：`YYYY-MM-DD HH:MM:SS dtim=<值>`，无异常则写"无"）

## 离线时间点列表
（从 STEP 4.1 step4_statemachine.json 中 toState=state_connection_lost 的时间点，每行一条）

## 电量结论
（"低电关机"或"电量充足，排除电量因素"）

## 埋点结论
（关键异常埋点摘要，或"无异常埋点"）
```

### 5.2 网络维度

**5.2.1 WiFi 信号强度分析**

**前置**：使用 Read 工具读取 `step5_device_summary.md`，提取"DTIM 异常时段"列表，用于本节末尾的交叉印证分析。

调用 `/api/v1/log-search/query/es`（Rule 7 ES 基础结构），`fuzzy_search` = `"\"/deviceMsg/status\" AND \"Rest\""`，保存至 `step5_status_p1.json`。分页处理见 Rule 8。

提取字段（从 `result.data.results` 中，`message` 字段为 JSON 字符串，解析其中）：`timestamp`（顶层）、`wifiRssi`、`signalLevel`、`ap`、`wifiChannel`。

> `statusList` 各 `name` 释义：`lark` MCP 查 [设备 statusList 信息](https://a4x-paas.feishu.cn/wiki/wikcnf0Fx4pMZCgBUJRq7eHe8Fb)。

**RSSI 信号强度判定：**

| RSSI (dBm) | 信号强度     |
| ---------- | ------------ |
| 0 ~ -50    | 极强          |
| -50 ~ -60  | 很强          |
| -60 ~ -70  | 良好          |
| -70 ~ -80  | 弱（可能影响连接） |
| -80 ~ -90  | 很弱（连接不稳定） |
| -90 以下   | 极弱/无信号    |

**异常判定（满足任一即为异常）：**

| 规则   | 条件                          | 说明              |
| ------ | ----------------------------- | ----------------- |
| 规则 1 | RSSI ≤ -70 的记录占比 > 50%    | 长期弱信号          |
| 规则 2 | RSSI 短时波动幅度 > 20dBm      | 信号不稳定          |
| 规则 3 | `ap` 字段在查询范围内变化       | 路由器切换          |

**关联分析**：将每次离线时间点与最近 status 记录匹配：

- 离线前 RSSI ≤ -80 → **弱信号导致离线**，建议设备靠近路由器或增加 AP/信号放大器
- 离线前 RSSI > -60 → 排除信号强度因素

> 与 5.1.2 DTIM 交叉（读取 `step5_device_summary.md` 中"DTIM 异常时段"）：DTIM < 7 时段与 RSSI 较低时段重合 → 佐证该时段网络质量下降。

**5.2.2 errorReports 错误报告分析**

调用 `/api/v1/log-search/query/es`（Rule 7 ES 基础结构），`fuzzy_search` = `"\"errorReports\""`，保存至 `step5_errors_p1.json`。分页处理见 Rule 8。

当 `message_parsed: true` 时，相关字段已解析为顶层字段，提取：`timestamp`、`reportGroup`、`httpsConnectDuration`、`version`、`firmwareType`、`errorReports`（数组，每项含 `reason`、`timestamp`）。

**错误分类：**`[err]` 前缀 = 实际故障，需重点关注；`[info]` 前缀 = 正常事件记录（如 webrtc 生命周期）。

`reason` 含义先通过 `lark` MCP 在以下飞书文档中检索（以文档结论为准；查不到时按字面兜底并说明「知识库未收录」）：

- `https://a4x-paas.feishu.cn/wiki/wikcntC4eqoHcfY3bFeS4NQ56ph?table=tblIBOiKbgJmvoLc&view=vewNN4WOgl`
- `https://a4x-paas.feishu.cn/wiki/wikcnWlWycIOpn5aLjEROKwilsh`
- `https://a4x-paas.feishu.cn/wiki/wikcn6GTvcTS3pjpUQPK4Gvfdve`

**统计分析**：遍历所有 `step5_errors_p*.json`，`[err]` 前缀的 `reason` 按组分类计数；`httpsConnectDuration` 统计最小/最大/平均值。

**异常判定（满足任一即为异常）：**

| 规则   | 条件                                    | 说明                   |
| ------ | --------------------------------------- | ---------------------- |
| 规则 1 | `[err]DNScache` 错误反复出现             | DNS 持续异常            |
| 规则 2 | `httpsConnectDuration` 均值 > 1000ms    | 网络延迟过高            |
| 规则 3 | `[err]` 级别错误集中在某一时段            | 可能网络中断或服务异常   |

**关联分析**（须已通过 lark 在飞书文档检索过 reason 含义）：

- 离线前 `[err]DNScache` → **DNS 解析故障**，建议检查路由器 DNS 配置或更换为 8.8.8.8
- 离线前 `httpsConnectDuration` > 1000ms 且 RSSI 正常 → **网络延迟/服务端响应问题**，非设备端信号问题
- 仅 `[info]` 级别记录 → 无连接错误，需从其他维度分析

> 查询结果中无 `[err]` 记录时，如实告知"errorReports 中未发现错误级别记录"，不得推测。

### 5.3 服务器维度

调用 `/api/v1/log-search/query/es` 查询服务端日志，关注：服务端主动断开连接记录、消息队列积压或超时记录。查询结果中无服务端异常时，如实告知"服务端日志未见异常"，不得推测。

**5.3 子代理产出物**

完成 5.3 查询后，将结论写入 `step5_server_summary.md`：

```
## 服务器维度结论
（"服务端日志未见异常"，或列出具体异常条目：时间、日志内容摘要）
```

### 5.4 输出结论

**前置**：使用 Read 工具读取 `step5_device_summary.md` 和 `step5_server_summary.md`，结合当前会话中 5.2 的查询结果，综合输出以下结论。

综合三个维度输出：

1. **根因判定**：基于实际日志数据给出最可能的离线原因，必须引用具体日志条目作为依据
2. **解决方案**：根据根因给出针对性建议
3. **无法定位时**：明确告知"当前日志数据不足以定位根因"，建议联系人工排查或提供更多信息（设备操作视频、路由器型号等）
