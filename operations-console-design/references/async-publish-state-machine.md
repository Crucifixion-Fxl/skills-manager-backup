# Async Publish State Machine — Compensation Worker 模式

本文件给 `operations-console-design` 补一层 `publish-flow-state-machine.md` 没覆盖的方法论：当**单条发布动作本身**就是一条跨越多个外部系统、单条 HTTP handler 包不住的长链路时，应该怎样把**执行过程**切成状态机，由后台 worker 异步推进，并在任一环节失败时做 compensation。

区分两层状态机很重要：

- `publish-flow-state-machine.md` 描述的是**业务流程**层面的状态机（`pending_staging → staging_ok → prod_ok → cancelled`），谁能触发转换、快照语义、审计现场还原。
- 本文件描述的是**发布动作执行过程**层面的状态机（`approving → s3_uploading → s3_uploaded → db_committed → notified`），侧重于长事务拆分、worker 推进、compensation、超时重试、idempotency。

两者是**嵌套关系**：一条 `PublishOrder` 在业务 state `pending_prod_approval` 或 `approving` 中时，内部可能再嵌套一个 async 执行 state machine 去实际完成 S3 上传 + DB commit + 通知。前者的 `approve-prod` 动作只是"点燃引信"，真正的编排由 worker 跑出来。

真实样例见 `/home/jchen/customer-care/admin/src/lib/approval-state-machine.ts` + `approval-worker.ts` + `app/api/publish-orders/[id]/approve-prod/route.ts`。每一节的方法论都可以在这三个文件里找到锚点。

## 1. 什么时候需要 async 化

同步发布够不够用，看几个触发信号。只要命中一条就值得切 async：

- **快照组装耗时超过 30s**。长条 HTTP 请求把 Vercel / Nginx / ALB 的超时逼近极限，用户点完按钮对着 loading 圈等两分钟就已经是 UX 灾难；一旦超时，半成品到底在哪个阶段、要不要重试，说不清楚。
- **外部审批是 webhook 形态**。飞书审批 / ServiceNow / Jira 服务台这类系统审批结果是通过异步回调通知的，同步 handler 根本没法阻塞等结果。
- **跨 DC / 跨 region 副作用**。US / EU / CN 三个 S3 bucket 依次上传 `rules.json` + `latest.json`，每一步 ~3–5s，正常也要 30s+；任一 region 失败还要回滚已成功的 region，把这整条流程塞进 HTTP handler 会让 handler 逻辑远超单一职责原则。
- **灰度 / 分批放量**。发布要按 tenant 或按百分比分批，期间每一批观察健康信号才决定是否放下一批，天然是长时间异步过程。
- **上游系统限流 / 重试窗口长**。被调用方（CMS、GB、Stripe）返回 429 或 503，同步 handler 只能立刻失败；worker 模式天然可以等几分钟后重试。

反过来，下列场景**不必**上 async state machine：

- 单条发布 < 5s 且只写 DB / 单个 S3 object，没有回滚诉求。
- admin 规模很小（每天 < 5 次发布），上 worker 反而把"把 HTTP 路径跑顺"的工程预算吃光。
- 发布结果立刻生效，没有"观察 + 确认"的第二阶段。

**一句话判断**：如果"发布这一动作"比"下一个 HTTP 请求能容忍的等待时间"更长，就该切 async。

## 2. 状态机需要哪些额外 state

sync 状态机里的"进行态 / 终态"二分在 async 版本里不够用。最小必要补两类字段：

### 2.1 `pending_*` 中间态（vs 业务终态）

在业务流程层面 `PublishOrder.status` 已经是终态 `prod_ok` 了，但 execution 层面可能还在 `db_committed` 往 `notified` 走。这两层 status **不能共用同一个字段**。

customer-care 的做法：

- `smart_popup_publish_order.status` 是业务 state，operator 看得到的；
- `smart_popup_rules_package.approval_state` 是 execution state，只有后台 worker + 超时告警关心。

execution 状态枚举推荐结构：

| 状态 | 类型 | 含义 | 是否 authoritative |
|------|------|------|-------------------|
| `approving` | 初态 | HTTP handler 刚创建 package，还没开始副作用 | 否，可回滚 |
| `s3_uploading` | 中间态 | 正在往对象存储上传 | 否，可回滚 |
| `s3_uploaded` | 中间态 | 对象存储写入成功，但 DB 还未 mark 为 authoritative | 否，可回滚 |
| `db_committed` | 中间态 | DB 已写入 authoritative 字段，发布实际生效 | **是，不可回滚**，只能向前到 `notified` 或 `failed` |
| `notified` | 终态（成功） | 所有副作用完成，包括 best-effort 通知 | 是 |
| `rolled_back` | 终态（回滚） | 只能从 `authoritative=false` 的 state 到达 | 是 |
| `failed` | 终态（放弃） | 重试预算耗尽后的人工介入态 | 是 |

关键约束：**一旦跨过 `authoritative` 边界（上例是 `db_committed`），就不允许 rollback，只能向前 notify 或 failed**。理由是 authoritative 之后的撤销需要 compensating write，而不是简单的 "把之前那个 write 删掉"，方法论上把它并入 rollback 会诱导错误设计。

### 2.2 轨道字段

推荐每条 execution row 至少配齐四个字段：

- `idempotency_key`（string, unique）— HTTP handler 从 header `Idempotency-Key` 取，worker 重跑不会 double-execute。
- `last_state_transition_at`（timestamp）— worker sweep 的 "grace window" 判据；超过 grace 还卡在中间态的 row 被当作 stuck。
- `retry_count`（int）— worker 每次推进失败自增一；超过 `MAX_RETRIES` 时 worker 把 row 推到 `rolled_back` 或 `failed` 而不是无限转。
- `created_by` / `operator`（string）— compensation / failed 的审计对象。

## 3. Compensation 语义

每一个**向前动作都要预定义对应的回退动作**，否则 worker 在中途失败只能卡住。customer-care 的规矩：

| 前进动作 | 回退动作 | authoritative 边界 |
|---------|---------|-------------------|
| HTTP handler 创建 `approving` row | 直接 `UPDATE ... SET state='rolled_back'`，订单回 `staging_ok` | 之前 |
| `approving → s3_uploading` | 同上 | 之前 |
| `s3_uploading → s3_uploaded` | 删除 S3 object；恢复 `latest.json` 指针（`rollbackLatestPointers()`）| 之前 |
| `s3_uploaded → db_committed` | **不可回滚**，只能前进到 `notified` 或标记 `failed` | **跨过** |
| `db_committed → notified` | Notify 失败不影响 authoritative，吞异常推到 `notified` | 之后 |

三条沉淀原则：

1. **回退动作必须幂等**。worker 重跑 compensation（S3 delete、pointer restore）不会造成额外副作用。
2. **回退动作自己也要走 state transition**，别只写 `state='rolled_back'`：还要同步 flip owning `PublishOrder.status` 回 `staging_ok` 并释放 `current_package_id`，否则 UI 上订单还显示 "approving" 但下面的 package 已经 rolled_back。
3. **过了 authoritative 边界就不回滚**。写进 DB / 改了 ownership / 通知过下游都意味着 "世界已经变了"，此时要么继续推进，要么人工介入 —— worker 不允许自动 undo。

## 4. Worker 架构

三种典型实现，按 admin 规模和成熟度选：

### 4.1 In-process `setInterval` / cron（单副本）

- customer-care 当前状态：`startApprovalWorker(intervalMs = 60_000)` 在 Next.js boot 时起，用 `setInterval` 跑 `runApprovalWorkerOnce()`。
- 适合：admin 只有 1–2 个 replica、发布量小、MVP 阶段。
- 风险：如果 admin 多副本，会有多个 worker 同时 sweep。customer-care 用 `UPDATE ... WHERE state IN (fromStates)` 的 `updateMany` 原子性防 race（`result.count === 0` 就表示输给了别的 worker，直接 return）。
- 迁移出口：GitLab issue 里记一条 "切 BullMQ / Vercel Cron"，等规模到了再切。

### 4.2 Queue-backed worker（BullMQ / SQS / Redis streams）

- handler 只做 "HTTP-fast"：写 DB、enqueue message、返回 200。
- 独立的 worker 进程消费 queue，每条消息触发一次 `advanceApprovalState(id)`。
- 适合：发布量中等、需要 retry-with-backoff、需要 DLQ 记录彻底失败的 row。
- 坑：消息 replay 必须配合 DB-level idempotency（`idempotency_key` 是 SSOT，不是 queue message id），否则 queue 重投会 double-execute。

### 4.3 离线 orchestrator（Dagster / Airflow job）

- 适合：发布流程本身带数据处理（批量 backfill、A/B 重算、回放分析），或需要人工节点（审批 checkpoint）。
- worker 不再在 admin 进程内跑，admin 只发起 / 轮询 run status。
- 坑：orchestrator 自己的 state 和 admin 的 state 是两份，要定期 reconcile；否则 orchestrator run 挂了但 admin 不知情。

**选型判据**：

- 单 replica + 每日 < 100 次 transition → in-process setInterval 够。
- 多 replica 或每小时 > 10 次 transition → queue-backed。
- 发布涉及批处理 / 人工 checkpoint → Dagster。

不要为了"看起来工程化"而过度一上来就上 queue —— in-process 方案容易调试、容易 grep 日志、失败重试语义更透明。

## 5. Idempotency

async worker 的基础要求：**每一步都是 upsert 语义，worker 重跑不会产生副作用**。

三层 idempotency：

### 5.1 HTTP 层

- HTTP handler 读 `Idempotency-Key` header（用户未传时后端自己生成一个 UUID 但**不回传**，以免客户端误以为这是"新的 key"）。
- 写 DB 前先 `SELECT ... WHERE idempotency_key = ?`；命中就直接返回已存在的 order，不新建。
- customer-care `approve-prod/route.ts` 实现：`req.headers.get("idempotency-key")` 取值，找到 existing package 就短路 return。

### 5.2 DB 层

- 所有 `state = X` → `state = Y` 的 UPDATE 必须带 `WHERE state IN (legalFromStates)`。
- `updateMany` 返回 `count`，如果 `count === 0` 说明输给了别的 worker / 状态已经被别人推进过，直接 return 而不是报错。
- customer-care `transitionPackage()` 封装这套语义：`for (const from of fromStates) assertTransition(from, to)` 先校验 transition 合法，再 `updateMany` 带 where 推进。

### 5.3 外部副作用层

- S3 `PutObject` 本身 idempotent（同 key 覆盖同 body 等价于 no-op）。
- GrowthBook 的 `createFeature` **不 idempotent**，要先 `GET`，不存在再 `POST`，存在就当作已完成。
- 飞书 approval instance 创建不 idempotent，`idempotency_key` 必须入库才能避免重复建审批。

**最小合约**：`advanceApprovalState(id)` 函数可以安全地被 handler、worker、operator 手动触发三处并发调用，最终状态机只推进一次。

## 6. 观测

async worker 最容易"看起来在跑实际卡住"，必须上三类埋点：

### 6.1 SLA metric

- 对每个 `pending_*` / 中间态，记录 `admin.publish.execution_state_duration{state=s3_uploading}` histogram。
- 告警阈值：`state_duration{state in (s3_uploading, s3_uploaded, db_committed)} > 5 * GRACE_MS`。
- 如果某个 state 的 duration p99 飙升，说明依赖系统（S3 / DB / 通知）在变慢，而不是 worker 挂了。

### 6.2 Compensation count

- `admin.publish.compensation_total{from_state, reason}` counter。
- 日常 baseline 不应为零（偶发网络波动会触发），但**不应持续增长**。
- 一旦 compensation 比例超过 5%，说明下游某个依赖变得不稳定，需要人工介入，而不是盲目扩 `MAX_RETRIES`。

### 6.3 Stuck-row alert

- 每 1min 跑一次 `SELECT count(*) FROM rules_package WHERE state IN transient AND last_state_transition_at < now() - 3 * GRACE_MS`。
- 大于 0 就是告警：worker 本身挂了或 transient row 卡在外部依赖里。

此外，**每一次 state transition 都写 audit log**（operator、action、from_state、to_state、retry_count、idempotency_key）。compensation / failed 不能只写日志不写 audit —— audit 是唯一能在事后 debug "这个 order 当时怎么挂的" 的现场。

## 7. 反模式

### 7.1 Fire-and-forget 不写 DB

- 现象：HTTP handler 直接 `Promise.resolve(doUpload()).catch(...)`，不落 DB。
- 后果：进程重启 / crash 后完全失联。用户点了按钮，handler 返回 200，副作用根本没跑。
- 正确做法：先写 DB 落 `approving` state，**然后**再 kick off async task；worker 遇到 orphan row 能接管。

### 7.2 Compensation 不写 audit

- 现象：回滚时只 `UPDATE state='rolled_back'`，不 `logAudit`。
- 后果：事后排查 "为什么这个订单回滚了" 只有时间戳没有原因，operator 反复追问。
- 正确做法：rollback / failed 的 transition 和 forward transition 同级别写 audit，`detail` 至少包含 `from_state / retry_count / last_error`。

### 7.3 Worker 拉取不加锁

- 现象：两个 replica 的 worker 同时 `SELECT ... WHERE state = 'approving'`，两边都拿到同一 row，都去做 S3 upload。
- 后果：S3 object 被 double-write（幸运），或 DB double-commit（不幸）。
- 正确做法：转换用 `UPDATE ... WHERE id = ? AND state IN (legalFromStates)` 配合 `updateMany.count`，`count === 0` 说明输给了别人。或者 SELECT FOR UPDATE SKIP LOCKED。

### 7.4 没有超时 reaper

- 现象：worker 只负责 "正常路径推进"，对卡在中间态的 row 不做清理。
- 后果：DB 里 `state = s3_uploading` 的 row 积累上千条，查询变慢，新请求串行等在它们后面。
- 正确做法：`runApprovalWorkerOnce()` 一定要先扫 `last_state_transition_at < now() - GRACE_MS` 的 row，按 `retry_count` 决定是继续推、回滚、还是 failed。

---

## 延伸阅读

- `publish-flow-state-machine.md` — 业务流程层的状态机
- `deployment-architecture.md` — 跨 DC / 跨 region 的 fan-out compensation 语义
- `growthbook-integration.md` §8 — Best-effort GB write + compensation-delete 的具体判定
- customer-care 源码锚点：`approval-state-machine.ts` / `approval-worker.ts` / `app/api/publish-orders/[id]/approve-prod/route.ts`
