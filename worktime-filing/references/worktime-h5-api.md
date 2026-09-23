# worktime-h5 API 参考（for worktime-filing skill）

Base URL: `https://emp.addx.live/worktime`
鉴权：所有请求带 `Authorization: Bearer $WORKTIME_TOKEN`（token 由用户在 `/auth/cli-login` 页面生成，有效期默认 90 天）

### 固定日期口径（2026-09-14）

系统名称：**研发工时和项目**。以下口径适用于网页与 worktime-filing skill，统一使用 GMT+8；日期时间戳为毫秒。

| 是否补填 | 填写日期 | 补填日期 | 工时归属 |
|---|---|---|---|
| 否（`false`） | 实际提交时间，由服务端接收提交时确定 | 空 | `填写日期`所在工作周 |
| 是（`true`） | 补填目标周内的日期；当前接口固定取目标周周五 | 实际提交时间，由服务端接收提交时确定 | `填写日期`所在的补填目标周 |

- 实际提交时间以该次填报首次被服务端接收的时间为核验基准。Skill沿用原任务轮询、查询或纠正记录时，不能自行以操作当下的时间替换。此条是操作与回读验收要求：管理端“重新入队”当前会生成新时间，不能假定该路径已经保留原提交时间；遇到此类记录须核对原任务与实际记录，并如实报告差异。
- 周统计只按`填写日期`计算；`补填日期`用于记录补填发生时间，不用于归周。是否需要补填应按用户确认的工作日期范围与当前周比较；历史周必须`is_retroactive=true`，不能因为今天提交而算进本周。
- 所有提交前必须展示 ISO 年周及完整起止日期。仅补填时与`week-numbers`接口核对；普通提交按 GMT+8 当前周核对，该接口不返回当前周，不能因未匹配当前周而阻止正常填报。系统旧周号与 ISO 周号不能混用：2026-08-31～2026-09-06 是 ISO W36、系统第35周；2026-09-07～2026-09-13 是 ISO W37、系统第36周。
- 例：9月7日补填8月31日～9月6日，`是否补填=true`，`填写日期=9月4日`，`补填日期=9月7日实际提交时间`；9月7日正常填写本周，则`填写日期=9月7日实际提交时间`、`补填日期`为空，属于9月7日～13日。
- 写入由提交接口生成这三个字段，skill只发送`is_retroactive`布尔值和补填时的`retroactive_iso_week_year`、`retroactive_iso_week_number`，不能自行直写飞书日期字段。完成后强制刷新并按返回记录ID回读，核对补填标记、归属周、原始提交时间和工时；时间证据不足时标记待核验，不猜测。
- 纠正已有记录前先核对目标周已有记录、总时长及项目和工作内容。内容不同的历史批次不能当重复数据删除，也不能自动把已有批次连续前移；保留记录，由本人确认并修改。个人修改接口当前不开放日期/补填标记变更；涉及跨周日期纠正时如实说明限制，不能推荐删除重填来掩盖原始日期。
- 已有记录经明确授权纠正时，原来是普通提交的，原`填写日期`才是需要保留的实际提交时间；原来已是补填的，保留原`补填日期`。不能用纠正操作时间覆盖这份历史事实。

## 1. 获取用户身份

```
GET /api/user/info
→ 200 {"code": 0, "data": {"user_id": "xxx", "open_id": "ou_xxx", "name": "张三"}}
```

## 2. 获取用户可选项目列表

```
GET /api/bitable/records?table_name=03- 项目档案表
→ 200 {"code": 0, "data": {"records": [
    {"record_id": "recXXX", "fields": {"项目名称": "iot-service-unified", "项目成员": [...], ...}},
    ...
]}}
```

- 后端自动按 `open_id` 过滤出"用户是项目成员"的项目。
- 只需用 `record_id` 和 `fields.项目名称` 做匹配；其他字段可忽略。

## 3. 获取目标周已填记录

```
GET /api/worktime/my-records?start_date=<目标周一 00:00:00 GMT+8 毫秒>&end_date=<目标周日 23:59:59 GMT+8 毫秒>&page_size=100
→ 200 {"code": 0, "data": {"records": [
    {"record_id": "recYYY", "fields": {
        "项目": [{"record_ids":["recXXX"],"text":"iot-service-unified"}],
        "工时": "4", "工作内容": "...", "填写日期": 1776000000000
    }},
    ...
]}}
```

时间戳算法（Python）：
```python
from datetime import datetime, timedelta, timezone
TZ = timezone(timedelta(hours=8))
is_retroactive = True  # 普通提交设为 False
iso_year, iso_week = 2026, 36  # 补填时来自用户确认且经 week-numbers 校验
now = datetime.now(TZ)
if is_retroactive:
    target_monday = datetime.fromisocalendar(iso_year, iso_week, 1).replace(tzinfo=TZ)
else:
    target_monday = now - timedelta(days=now.weekday())
start = target_monday.replace(hour=0, minute=0, second=0, microsecond=0)
end = start + timedelta(days=7) - timedelta(milliseconds=1)
start_ms = int(start.timestamp() * 1000)
end_ms = int(end.timestamp() * 1000)
```

### 3.1 获取目标周工作日历上限

```http
GET /api/worktime/week-info?date=<目标周周一 YYYY-MM-DD>
```

响应 `data.max_hours` 是该目标周允许的总工时上限。常规周通常为 40 小时，节假日或调休周可能不同；Skill 生成、迭代、修改和提交前都必须以该值计算剩余额度。接口失败或返回值无效时应停止写入，不能回退到固定 40 小时。

## 4. 获取可补填周（用于展示与交叉校验）

```
GET /api/worktime/week-numbers
→ 200 {"code": 0, "data": {"week_numbers": [{
  "value": 35,
  "label": "35周(08.31-09.06)",
  "iso_year": 2026,
  "iso_week_number": 36,
  "week_start": "2026-08-31",
  "week_end": "2026-09-06",
  "fill_date": "2026-09-04"
}]}}
```

`value` 是旧 H5 的 0 基内部周序号，只供旧前端使用。Skill 必须把列表中的 `iso_year` 映射到提交字段 `retroactive_iso_week_year`、把 `iso_week_number` 映射到 `retroactive_iso_week_number`，并把精确日期范围展示给用户确认。

## 5. 提交五件事

```
POST /api/worktime/submit
Content-Type: application/json

{
  "records": [
    {
      "project_record": {"record_id": "recXXX"},
      "hours": 12,
      "work_content": "完成 A 接口、对接 B 模块"
    },
    ...
  ],
  "is_retroactive": false
}
→ 200 {"code": 0, "data": {"task_id": "uuid-xxx", "total": 5, "status": "processing"}}
```

**补填**（填历史周）时加：
```json
"is_retroactive": true,
"retroactive_iso_week_year": 2026,
"retroactive_iso_week_number": 36
```

用户说“第 N 周”时按 ISO-8601 周（周一到周日）理解。禁止把 ISO 周数传给旧字段 `retroactive_week_number`。

补填入队成功响应还会在 `data.retroactive_week` 返回后端实际解析的周：

```json
{
  "code": 0,
  "data": {
    "task_id": "uuid-xxx",
    "total": 5,
    "status": "processing",
    "retroactive_week": {
      "iso_year": 2026,
      "iso_week_number": 36,
      "worktime_week_number": 35,
      "week_start": "2026-08-31",
      "week_end": "2026-09-06",
      "fill_date": "2026-09-04"
    }
  }
}
```

Skill 必须在轮询前核对它与用户确认的周范围一致。不一致时标记契约错误，但任务已经入队，仍要用 `task_id` 轮询到终态并回读实际结果，不能把停止轮询当作取消。

后端约束：
- 每周最多 5 条
- 每条工时 > 0，步长 0.5
- 每条工时必须为正数且按 0.5 小时递增；周总和不得超过该周工作日历上限（常规周为 40 小时）
- `record_id` 必须存在于项目档案表
- 5 秒内重复提交会被频率限制拒绝（返回 `error: 提交过于频繁...`）；这不是持久幂等保证
- 当前只允许补填当前 ISO 年内的历史周；未来周和跨 ISO 年请求必须由 Skill 在提交前拦截

## 6. 轮询提交结果并回读验证

```
GET /api/worktime/submit/status/<task_id>
→ 200 {"code": 0, "data": {
    "status": "completed" | "processing" | "failed",
    "total": 5, "success": 5, "failed": 0,
    "submitted_records": [{"record_id": "recZZZ", ...}, ...],
    "error": null | "错误原因"
}}
```

推荐轮询节奏：前30秒间隔1.5秒，之后间隔3秒继续查询原task_id，30秒不是提交失败的界限。`status=completed` 或 `failed` 时停止。

超过 30 秒仍为 `processing`或状态查询结果不明时，将本次操作标记为 `UNKNOWN/PENDING`，保留 `task_id`，后续只继续查询同一任务并回读，不得重发 POST。跨周批次的 POST 已发出但响应丢失时，用预先保存的 `operation_key` 走 owner-scoped 按键状态查询，找回原 `task_id`；不生成新键，不重发入队。

跨周批次的能力与幂等协议：

```http
GET /api/worktime/capabilities
→ 200 {"code":0,"data":{"version":1,"retro_batch_operation_key_v1":true}}
```

- Skill 只有在上述版本与能力均精确匹配时才能生成批量候选；旧后端、能力缺失/false/查询失败都必须在 0 POST 时关闭批量。
- 每周提交在 JSON 中增加 `operation_key` 与 `snapshot_fingerprint`。`operation_key` 是至少 128 bit 熵的 CSPRNG 不透明值，不得包含人员标识、Token 或工作内容；服务端从认证会话取用户，不信任客户端身份字段。
- `snapshot_fingerprint` 是下列规范对象的 SHA-256 小写 hex：`{"v":1,"iso_year":YYYY,"iso_week":N,"max_half_hours":M,"existing":[...],"records":[...]}`。所有字符串先做 Unicode NFC；`existing` 按工时记录 ID 升序，每项只含 `record_id/project_record_id/half_hours`；`records` 按 `project_record_id` 升序，每项只含 `project_record_id/half_hours/work_content`；工时一律换算为 0.5 小时单位的整数。JSON 键按 Unicode 码点升序，UTF-8 编码，不转义非 ASCII 字符，分隔符为 `,`/`:` 且无多余空白。服务端要从经过业务校验的实际请求和当前周状态重算并安全比较，不信任客户端自报指纹。
- 服务端在入队前原子、持久化绑定 `(认证用户, ISO 年/周, snapshot_fingerprint, operation_key, task_id/status)`，且该状态必须跨进程/重启保留。同键+同绑定返回原 `task_id/status` 而不重复入队；同键不同绑定或同一用户+周已有 in-flight/UNKNOWN 却使用不同键时返回 409。
- 服务端 worker 必须在标记可释放终态前，自行对权威存储核对实际写入的记录 ID、条数和目标周归属；进程崩溃/重启后必须从持久绑定恢复对账。只有服务端对账得到 `server_verified` 或 `failed_reconciled` 才释放该用户+周的 in-flight 锁；在此之前不得为新指纹/新键入队。Skill 仍须独立强制回读，不以服务端对账替代用户可见验证。

跨周单周提交的新字段与回显：

```http
POST /api/worktime/submit
Content-Type: application/json

{"records":[...],"is_retroactive":true,
 "retroactive_iso_week_year":2026,"retroactive_iso_week_number":35,
 "operation_key":"<opaque-csprng>","snapshot_fingerprint":"<sha256>"}
→ 200 {"code":0,"data":{
  "task_id":"uuid-xxx","status":"processing",
  "operation_key":"<same>","snapshot_fingerprint":"<same>",
  "iso_year":2026,"iso_week_number":35,
  "operation_status":"in_flight","retroactive_week":{...}
}}
```

响应丢失时按键恢复（始终使用当前 `Authorization` Token）：

```http
GET /api/worktime/submit/status/by-operation-key
X-Worktime-Operation-Key: <opaque-csprng>
→ 200 {"code":0,"data":{
  "task_id":"uuid-xxx","status":"processing|completed|failed",
  "operation_key":"<same>","snapshot_fingerprint":"<same>",
  "iso_year":2026,"iso_week_number":35,
  "operation_status":"in_flight|unknown|server_verified|failed_reconciled"
}}
```

按键查询只能返回当前认证用户的绑定；他人键与不存在键统一 404。所有回显字段必须与快照精确一致，否则立即停批。

补填任务 `completed` 后，只有 `failed=0`、`success=total=计划提交数`、`submitted_records` 条数等于 `success`，并且用目标 ISO 周的周一 00:00 到周日 23:59:59（GMT+8）调用第 3 节的 `my-records` 后能按全部 `submitted_records[].record_id` 回读、回读条数等于 `success`、每条 `填写日期` 等于该周周五，才可宣告成功。若入队响应的周范围不一致，还需同时回读响应所示实际周并报告真实写入结果；任一条件不满足都报告失败或部分失败。

### 6.1 跨周批量补填编排

服务端没有跨周原子批量端点。Skill 对两个或以上历史 ISO 周执行批量补填时，必须把每周作为独立的 `/api/worktime/submit` 任务串行编排：

1. 候选前精确校验 `capabilities` 的 v1 持久 operation-key 能力；不满足时 0 POST 关闭批量。目标列表去重升序后单批必须为 2–8 周；超过 8 周时先停在候选生成前，按时间连续、组大小相差不超过 1 的方式分成 2–8 周子列表，不得产生单周尾组（9 周例如 5+4），让用户选择先预览哪组；每组独立完整预览和确认。一次获取最新 `week-numbers`，每周按 ISO 年/周和完整日期范围交叉校验；仅支持当前 ISO 年内、早于当前周且在响应中存在的周。
2. 每周独立查询 `my-records?force_refresh=true` 和 `week-info`，独立计算最多 5 行、已有工时、动态上限和剩余额度。任何一周不可验证时，整批不得进入确认。
3. 展示全部周的完整候选，同时明示“串行非原子，后续周失败时已成功周不回滚”，再要求新的顶层消息确认同一完整周列表。普通 `yes`、缺少/增加周或变更周序都无效。快照还要私下绑定 `/api/user/info` 的当前用户身份，并为每周生成至少 128 bit 熵的 CSPRNG 不透明 `operation_key`绑定该周候选指纹；键和人员标识均不展示/记录。
4. 确认后先重取能力版本、用户身份、填报权限、项目白名单和 `week-numbers`，再对全部尚未开始周重新读取 `my-records?force_refresh=true` 与 `week-info`；预览状态变化时不发送 POST，而是重新预览并确认。
5. 从最早周开始，每周发送一个带 ISO 年/周、`operation_key` 与 `snapshot_fingerprint` 的 POST，严格核对回显的键/指纹/周，再轮询到服务端已权威对账的终态、完成客户端回读验证后处理下一周。进入每个后续周前再次刷新该周的本人记录与 `week-info`，相对批次快照变化就停止。两次 POST 至少间隔 5 秒，不得并发。
6. 任一周失败、部分成功、契约不一致或回读不完整时立即停止后续周，并逐周返回 `成功 / 失败或部分成功 / 未执行`。该编排不是事务，已经成功的周不会回滚；恢复前先强制回读并排除已成功记录，剩余 2–8 周按新批次、仅 1 周按单周补填处理，均需重新完整预览和新确认。
7. 预览后出现 401 必须停批并使快照/确认失效；重新登录后仍要复核 `/api/user/info`、权限、项目白名单与剩余周数据，不得更换 Token 后续跑原批次。任务超时则保留原 `task_id`，响应丢失则使用原 `operation_key` 从 `GET /api/worktime/submit/status/by-operation-key` 恢复原任务；两者都停止后续周、不生成新键或重复入队。服务端须持久阻止同一用户+周的并发不同键，直到 worker 对权威存储完成记录/周归属对账并得到可释放终态。

输入内容也必须按证据日期或用户明确标注归入唯一周。禁止把一份内容复制到所有周、平均摊分总工时，或把无法归属的内容默认放进最后一周。

## 7. CLI 本地 OAuth 登录（推荐，自动化）

**脚本入口**：`python3 ~/.claude/skills/worktime-filing/login.py` — 启本地端口、打开浏览器、自动回调写入 `.env`。

**后端配合路由**：`GET /auth/cli-redirect?local_port=<PORT>&state=<STATE>`
- 参数校验：`1024 ≤ local_port ≤ 65535`；`8 ≤ len(state) ≤ 128`。
- 未登录 → 存 `session['cli_redirect_pending']={local_port,state}` → 302 到飞书 OAuth。
- 已登录 → `cli_token_manager.create(user_info, name='local-cli-login', expiry_days=90)` → 302 到 `http://127.0.0.1:<PORT>/callback?token=wktok_xxx&state=<STATE>&name=<用户名>`。
- `auth_callback` 若检测到 `cli_redirect_pending`，登录完成后自动跳回 `/auth/cli-redirect`。

**本地脚本责任**：校验回调 state、保存 token、关闭 server。

## 8. Token 管理后台（可选，手动）

| 接口 | 方法 | 说明 |
|---|---|---|
| `/auth/cli-login` | GET（浏览器） | 管理页面：列出 / 撤销 / 手动创建 |
| `/api/auth/cli-tokens` | GET | 列出当前用户所有 Token |
| `/api/auth/cli-tokens` | POST | 手动创建（备用；正常流程走 login.py） |
| `/api/auth/cli-tokens/<id>` | DELETE | 撤销 Token |

常规用户不需要用这些接口，`login.py` 会自动处理。

## 9. 常见错误

| HTTP | code | msg | 处理 |
|---|---|---|---|
| 401 | 401 | 未登录 | Token 失效/过期，引导重新生成 |
| 400 | 400 | 没有要提交的记录 | records 为空或格式错误 |
| 400 | 400 | 用户记录ID不能为空 | （非 submit 接口）缺必填参数 |
| 500 | 500 | 多维表格服务未初始化 | 后端异常，重试或联系管理员 |

提交后 `task_id` 查询返回 `status=failed` 时的 `error` 常见值：
- `记录包含无效的项目 record_id`
- `工时不是正数或未按 0.5 小时递增`
- `提交过于频繁，请等待 X 秒后再试`
- `项目不在你的项目成员列表中`

## 10. 修改指定 ISO 周内的本人记录

```http
PATCH /api/worktime/my-records/<record_id>
Content-Type: application/json

{
  "iso_year": 2026,
  "iso_week": 36,
  "hours": 8,
  "work_content": "可选；不修改则省略",
  "project_record_id": "recXXX"
}
```

- `iso_year` 和 `iso_week` 必填，用来校验记录的业务归属周；不匹配返回 409，后端不会移动记录到另一周。
- `hours`、`work_content`、`project_record_id` 至少提供一个；只能修改这三个业务字段。
- 后端仅允许记录所有者修改，项目必须仍在本人项目成员白名单，工时步长 0.5，修改后周总工时不得超过该周上限。
- 响应 `data.stats_synced` 表示当前周的人员统计是否已同步为精确合计；`false` 不回滚主记录，调用方必须提示需要管理员修复。
- 成功后用同一 ISO 周范围调用 `my-records?force_refresh=true` 回读校验。

## 11. 删除 8 天内的本人记录

```http
DELETE /api/worktime/delete-record/<record_id>
```

后端同时校验当前 Token 用户是记录所有者，且规范化后的 `填写日期` 年龄满足 `0 ≤ age ≤ 8×24h`。Skill 必须先展示删除预览并等待新的顶层用户消息明确确认；成功后强制刷新原周并确认记录消失。响应 `data.stats_synced=false` 表示当前周人员统计同步失败，主记录仍已删除。

## 12. 管理端按名称分配项目

预览：

```http
POST /api/worktime/admin/assign-project
Content-Type: application/json

{"person_name":"张三","project_name":"GS001","confirm":false}
```

预览成功返回不透明的 `data.confirmation_token`，同时返回可展示的 `person_name`、`project_name` 与 `scope`。Skill 只保存令牌，不展示、不解析、不记录。

用户确认后保持人员名和项目名不变，将 `confirm` 改为 `true` 并原样回传令牌：

```json
{"person_name":"张三","project_name":"GS001","confirm":true,"confirmation_token":"<opaque>"}
```

后端从当前 Token 确定操作人，只在操作人获授权的部门范围内解析目标人员：

- 未登录：401；操作人没有管理入口权限：403。
- 授权范围内找不到或目标在范围外：统一 404（不泄露范围外同名人员是否存在）。
- 人员或项目名称不唯一，预览令牌缺失/过期，或人员、权限、项目在预览后发生变化：409；前者需提供完整名称，其余情况需重新预览确认。
- 已在项目中：200 且 `data.already_assigned=true`，视为幂等成功。

其他历史写入口也有相同的目标人员权限校验，但自动化应只使用上述专用端点。

## 13. 授权范围内的管理查询

### 13.1 项目成本

```http
GET /api/project-cost/data
GET /api/project-cost/data?month=2026/09
GET /api/project-cost/data?quarter=2026-Q3
```

只有服务端配置的项目成本授权用户可以访问。`data.aggregates[]` 的 `project_name`、`hours` 用于项目汇总；响应同时提供 `cache_updated_at` 和 `cache_age_sec`。403 后禁止切换身份或用他人 Token 重试。

### 13.2 部门填写情况

```http
GET /api/batch-import/department-records
```

服务端根据当前 Token 返回所有授权部门，或仅返回操作人所属部门。任何 `view_as_open_id` 代查参数都会被服务端拒绝；Skill 也不得传入。关键结构：

```json
{
  "data": {
    "all_departments": false,
    "departments": [{
      "department_name": "研发一部",
      "member_summary": [{
        "filler_name": "张三",
        "filler_open_id": "ou_xxx",
        "all_records": [{"项目":"GS001","工时":8,"填写日期":"2026-09-04"}]
      }]
    }]
  }
}
```

判断“本周未填”必须按 GMT+8 当前 ISO 周范围检查每个成员的 `all_records[].填写日期`；不能使用包含历史记录的 `total_record_count`。跨部门去重可在内存中使用 `filler_open_id`，但最终回答不要输出 open_id。
