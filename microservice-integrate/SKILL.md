---
name: microservice-integrate
description: 后端微服务"架构咨询 + 平台接入"双能力。当开发遇到 (1) 架构问题——"我需要 X 能力调哪个服务"、"这服务属于哪一层"、"新建仓应该放哪个 GitLab group"、"跨服务调用走 SDK 还是 REST"、"feature flag/支付/通知/测试用户应该怎么用"——基于本仓 public/dev-standards/architecture/backend-service-architecture.html 给权威答案；或 (2) 已确定要接入某个内部微服务（权益中心 / 个性化引擎 等），根据接入规格 + 当前项目技术栈生成接入代码。触发词："架构"、"分层"、"找哪个服务"、"调谁"、"放哪个 group"、"GitLab group"、"调用规则"、"接入"、"对接"、"integrate"。
---

# Microservice Integrate — 架构咨询 + 接入引导

两种用途：

| 模式 | 用途 | 何时触发 |
|---|---|---|
| **A. 架构咨询**（不写代码，回答问题）| 帮开发理解服务清单、分层、调用规则、GitLab group 规范、横向能力如何使用 | 用户问"找哪个"/"哪一层"/"放哪个 group"/"应该怎么用" |
| **B. 接入代码生成**（写代码）| 已确定接入哪个平台，生成 client / consumer / 韧性 / 配置 代码 | 用户带具体平台参数（如 `/microservice-integrate entitlement`）|

---

## 描述 — 架构 SSOT（所有架构问题先查这里，不要凭记忆）

| 文档 | 用途 |
|---|---|
| [`backend-service-architecture.html`（本仓 `public/dev-standards/architecture/`）](../../public/dev-standards/architecture/backend-service-architecture.html) | **服务清单 / 5 层架构 / 调用规则 / GitLab group 命名约定 / 仓库索引** —— 全局架构 SSOT（2026-08-11 从 `engineering/architecture` 仓迁回本仓,HTML 正本;人读版在 [Pages](https://pages.addx.ai/engineering/skills/dev-standards/architecture/backend-service-architecture.html)）|
| [`docs/architecture/backend-service-tdd-adoption.md`](../../docs/architecture/backend-service-tdd-adoption.md) | TDD 接入 / routing-key / **测试用户与 qatools 协同** |
| [`docs/architecture/domain-model.md`](../../docs/architecture/domain-model.md) | 业务术语 SSOT |
| [`docs/architecture/tdd-infra/`](../../docs/architecture/tdd-infra/) | TDD 通用方法论（方案 A/B / partition key / Mixed Workload）|

**铁律**：架构问题不要靠记忆答。**先读上面文档**（全部在本仓,直接 Read——`backend-service-architecture.html` 在 `public/dev-standards/architecture/`,`domain-model.md` / `backend-service-tdd-adoption.md` / `tdd-infra/` 在 `docs/architecture/`），答案要引具体章节（§N.x）和具体服务名。文档未覆盖的场景，明确告知"文档未覆盖"，不编造。

---

## A 模式 — 架构咨询

### A.1 "我需要 X 能力，应该调哪个服务？"

按能力反查（详细职责见 [架构 §3-§6](../../public/dev-standards/architecture/backend-service-architecture.html)）：

| 我要做的事 | 找哪个服务 | 在哪一层 |
|---|---|---|
| 用户身份 / 鉴权 / 用户基础属性 | **用户中心 UC** | 基础平台 |
| 订阅 / 一次性付费 / 退款 / 权益校验 | **增值订阅 SUB** —— 业务方**不直连** Stripe/Apple Pay/Airwallex | 基础平台 |
| 用户画像 / AB 实验评估 | **Personalization Engine** —— 业务方**不直连** GrowthBook | 基础平台 |
| Feature Flag / 实验分流 | 通过 **PE 的 evalFeature()** 调（不直连 GrowthBook） | 基础平台 |
| 设备绑定 / 解绑 / 共享 / 设备列表 | **设备管理 DM** | C 端业务平台 |
| 固件升级 / 灰度 / 回滚 | **OTA**（剥离中，当前在 iot-service-unified 内） | C 端业务平台 |
| 录像 / 回看 / AI 事件片段 | **录像回看 REC** | C 端业务平台 |
| 直播 / 对讲 / PTZ | **直播 LIVE**（业务）+ **kiss**（信令）| 业务平台 + 基础平台 |
| 增值导购 / Paywall / 转化漏斗 | **Engagement** | C 端业务平台 |
| 弹窗 / Rating / 保修 / 差评拦截 | **Customer Care**（SmartPopup 含 Rating 子形态）| C 端业务平台 |
| 设备消息上下行（属性 / 事件 / 命令）| **IoT 平台** | 基础平台 |
| 设备能力 schema 注册 / 校验 | **物模型平台** | 基础平台 |
| 视觉识别 / 人形检测 / LLM 对话 | **AI 推理服务**（统一 endpoint，规划中）| 基础平台 |
| 接入网关 / 鉴权 / 限流 | **APISIX** | 共享基础 |
| 内容管理 / 多语言素材 / Paywall 文案 | **Payload CMS**（全公司通用，**不是营销专属**）| 共享基础 |
| 通知（Push / 邮件 / 短信 / In-App）| **Novu**（立项中）—— 业务方**不直连** FCM/APNs/SendGrid | 共享基础 |
| 埋点上报 | **Tracker SDK → Snowplow Collector** | 共享基础 |
| 错误监控 / 指标 / 日志 / Dashboard | **可观测性平台**（Sentry/Grafana/Prometheus/OTel）| 共享基础 |

**关键模式**："业务方不直连 X，必须走 Y 包装层" —— 5 处典型抽象：
1. **GrowthBook → PE**：换 feature flag 平台业务无感
2. **支付通道 → SUB**：避免 N 家业务各接 Stripe/Apple Pay
3. **FCM/APNs → Novu**：通道切换无感、订阅偏好统一
4. **OAuth/JWT → APISIX 鉴权 plugin**：业务服务不解 token
5. **设备协议 → IoT 平台 / kiss**：业务不直接处理 MQTT

### A.2 "这个服务属于哪一层？"

5 层结构 → [架构 §1 + §2](../../public/dev-standards/architecture/backend-service-architecture.html)：

| 层 | 含义 | 自研? | 行业绑定? | 当前成员 |
|---|---|---|---|---|
| 🎯 垂直产品系统 | 完整 C 端产品（自带 App/Hub/Admin）| ✅ | 强 | Naturehood · 安防告警 · Golf |
| 🧩 C 端业务平台 | 横向业务能力，含业务语义 | ✅ | 强 | DM · OTA · REC · LIVE · Engagement · Customer Care |
| ⚙️ 基础平台 | 自研 × 行业绑定，业务侧调它无业务语义 | ✅ | 弱 | IoT · kiss · 物模型 · AI · PE · UC · SUB |
| 🔧 共享基础服务 | 开源/SaaS × 行业通用 工具 | ❌ | ❌ | APISIX · Payload CMS · GrowthBook · 埋点 · Novu · 可观测性 |
| 🏗️ 公司基础设施 | K8s / Kafka / MySQL / S3 / DW | — | — | — |

**判定原则**：
- **垂直产品 vs 业务平台**：有没有专属 C 端入口（自带 App/Hub/Admin）
- **业务平台 vs 基础平台**：业务侧调它的接口语义有没有业务感（`getUser` / `evalFeature` 是无业务感 → 基础；`createTouchpoint` 是业务感 → 业务平台）
- **基础平台 vs 共享基础**：自研×行业绑定 vs 开源×通用——能不能换厂家无感（PE 包了 GrowthBook，业务能换 GrowthBook）

### A.3 "新建一个服务，应该放在哪个 GitLab group？"

目标命名约定（[架构 §1.x GitLab group 命名约定](../../public/dev-standards/architecture/backend-service-architecture.html)）：

| 架构层 | 目标 GitLab group |
|---|---|
| 垂直产品 | **`applications/`** |
| C 端业务平台 | **`services/`** |
| 基础平台 | **`platform/`** |
| 共享基础服务 | **`infra/`** |
| 公司基础设施 | `ops/` / `k8s/` |

**新建服务**：必须按目标约定。**老服务**：当前 `CLOUD/` / `THIN/` 还有 10 个不对齐的 ⚠️，按架构 §1.x 迁移表渐进迁。

### A.4 "我跨服务调用，应该走 SDK 还是 REST？"

[架构 §1 调用规则](../../public/dev-standards/architecture/backend-service-architecture.html)：

| 调用方向 | 协议 | 备注 |
|---|---|---|
| **同层之间**（业务平台↔业务平台、基础平台↔基础平台）| **必须 SDK**，禁止裸 REST | 整体技术架构 wiki 硬约束 |
| **向下调用**（业务平台 → 基础平台 / 共享基础）| SDK 优先，REST 也行 | — |
| **向上调用**（基础平台 → 业务平台）| **禁止** | 架构纪律硬约束 |
| **App / Web → 后端**| **必经 APISIX 网关**，不直连业务服务 | — |
| **业务服务 → 第三方（Stripe / FCM / OpenAI）**| **走包装层**（SUB / Novu / AI 推理）| 详见 A.1 5 处典型抽象 |

### A.5 "Feature Flag / AB 实验应该怎么用？"

**走 Personalization Engine 的 `evalFeature()`**，不直连 GrowthBook。理由 + 落地 → [架构 §5.5 PE](../../public/dev-standards/architecture/backend-service-architecture.html)。

### A.6 "支付（Stripe / Apple Pay / Airwallex）应该怎么用？"

**走增值订阅 SUB 的 `createOrder` / `checkEntitlement`**，业务方不直连任何支付通道。理由 + 落地 → [架构 §5.7 SUB](../../public/dev-standards/architecture/backend-service-architecture.html)。

### A.7 "通知（Push / 邮件 / 短信）应该怎么用？"

**走 Novu**（立项中）。业务方调 Novu workflow API，不直连 FCM/APNs/SendGrid。理由 + 落地 → [架构 §6.5 Novu](../../public/dev-standards/architecture/backend-service-architecture.html)。

### A.8 "测试用户怎么造？测试设备怎么 lease？"

**统一走 qatools** —— `https://qa-tools-staging.addx.live`，对应仓库 [`CLOUD/qa-tools`](https://gitlab.addx.ai/CLOUD/qa-tools.git)：

| 场景 | 怎么做 |
|---|---|
| 创建测试用户 | qatools `/api/test-users` 注册——服务端生成 `test-{branch_slug}-{ts}-{nonce}` prefix；**业务测试代码禁止自己注册** |
| 测试设备 | qatools `/api/test-devices` 从 B 端产测/MES 预留的 `TEST-BATCH-` 池 lease；**禁止造假 mock 设备** |
| 用户数据 reset | qatools `/api/test-users/{id}/reset?scope=...`；**业务测试代码禁止跑 DELETE** |
| 测试用户 cleanup | **不写 per-test cleanup**——靠 prefix 命名 + 中心化 GC job |

完整策略 + 风险防御 → [TDD adoption §3 测试用户与 partition key 策略](../../docs/architecture/backend-service-tdd-adoption.md)。

### A.9 "我做的服务以后怎么接 sandbox 测试 (routing-key)？"

每个服务的 routing-key 接入清单 → [TDD adoption §2.2 接入清单（每个服务的具体改造点）](../../docs/architecture/backend-service-tdd-adoption.md)。

按服务复杂度 3 档：🟢 零改动（OTel 自动）/ 🟡 中（Kafka producer + cron 注入）/ 🔴 大（webhook 反查 + 多通道叠加）。

### A 模式工作流程

收到架构问题时：

1. **先 grep / Read 架构文档**找答案，不要凭记忆
2. **答案必须引具体章节 + 服务名**（如"按 §1 调用规则，业务平台之间必须走 SDK"）
3. **覆盖不全直说**："架构文档未覆盖此场景，建议先去 wiki 找 owner / 在飞书架构群问"，不编造
4. 如果用户问的是 **routing-key / 测试用户 / qatools**，立刻引到 [TDD adoption 文档](../../docs/architecture/backend-service-tdd-adoption.md)
5. 如果用户问 **TDD 通用方法（方案 A/B、Mixed Workload、partition key）**，引到 [tdd-infra/service-integration-tdd.md](../../docs/architecture/tdd-infra/service-integration-tdd.md)

---

## B 模式 — 接入代码生成

> **如果是架构咨询，请走 A 模式**。本模式只在用户明确要接入某个具体平台时使用。

### 可用平台

| 平台 | 参数 | 说明 |
|------|------|------|
| 权益中心 | `entitlement` | vip-service 权益模块，Kafka 事件 + gRPC 查询 |
| 个性化服务 | `personalization` | personalization-engine，HTTP/gRPC 实验评估 |

新增平台：在 `references/<platform>.md` 添加规格 + 在上表加一行。

### Step 0：选择微服务

列出可用微服务表格，让用户选择。如果已通过参数指定（`/microservice-integrate entitlement`），跳过此步。

### Step 1：识别当前项目技术栈

检查工作目录：
- `go.mod` → Go
- `pom.xml` / `build.gradle` → Java
- `package.json` → Node.js
- `pubspec.yaml` → Flutter/Dart
- `pyproject.toml` / `requirements.txt` → Python
- 其他 → 提示用户确认

### Step 2：读取平台接入规格

读取 `~/.claude/skills/microservice-integrate/references/<platform>.md`。文件不存在 → 告知用户规格未录入，提示可贡献 `references/<platform>.md`。

### Step 3：分析现有接入状态

```bash
bash ~/.claude/skills/microservice-integrate/references/verify.sh <platform> <project_root>
```

- 全部 PASS → 已完整接入
- 部分 PASS → 列已完成 / 未完成
- 全部 FAIL → 全新接入

### Step 4：生成接入代码

按平台规格生成（按需选取）：
1. **依赖引入**：go.mod / pom.xml 添加 SDK
2. **Client 初始化**：gRPC/HTTP client，含超时
3. **事件消费**（Kafka）：consumer + 消息处理
4. **韧性策略**：超时（timeout budget）+ 重试（exponential backoff）+ 降级（fallback）
5. **业务处理骨架**：核心调用流程留 TODO
6. **可观测性**：structured log（service / method / duration / error）+ 传播 trace context
7. **配置项**：服务地址 / topic 名 从配置读取，按环境区分

**原则**：
- **适配项目风格**：参考已有 Kafka consumer / gRPC client 写法
- **不硬编码配置**：从配置文件 / 环境变量读取
- **幂等处理**：平台要求幂等就生成幂等机制
- **错误处理**：按平台规格的错误码表区分可重试 / 不可重试
- **认证适配**：mTLS / JWT / API Key 按规格配，凭证从 secret manager / 环境变量读

### Step 5：编译与测试

1. 编译通过（`go build ./...` / `mvn compile`）
2. 平台规格有测试建议 → 生成测试代码并运行通过

### Step 6：运行验证脚本（强制，不可跳过）

```bash
bash ~/.claude/skills/microservice-integrate/references/verify.sh <platform> <project_root>
```

脚本自动从 `.md` 的 checklist 表格提取 `auto` 验证规则执行 grep 校验。

处理流程：
1. 跑脚本 → 看输出
2. 有 ❌ FAIL → **立即修复 + 重新跑脚本**
3. 重复直到 exit 0
4. 把验证报告 + ⚠️ 待人工确认项展示给用户

**禁止**：不跑脚本就声称验证通过；脚本 exit 1 时不得结束 skill。

---

## 规则 — 模式判断（用户输入特征 → 触发模式）

| 用户输入特征 | 模式 |
|---|---|
| 含具体平台参数（`/microservice-integrate entitlement`）| **B**（直接生成代码） |
| 问"应该用什么服务" / "找谁" / "调谁" | A.1 |
| 问"哪一层" / "属于" / "分层" | A.2 |
| 问"放哪个 group" / "建仓" / "GitLab" | A.3 |
| 问"调用方向" / "SDK vs REST" | A.4 |
| 问"feature flag / AB / 实验" | A.5 |
| 问"支付 / Stripe / Apple Pay" | A.6 |
| 问"通知 / push / 邮件 / Novu" | A.7 |
| 问"测试用户 / qatools / 测试设备" | A.8 |
| 问"sandbox / routing-key / 接入纪律" | A.9 |
| 不确定 | 先归 A 模式，澄清需求后再决定 |

---

## Examples

### Good — A 模式（架构咨询）

**用户**："我做新功能要 AB 测试，怎么接？"

✅ 我：先查 [`backend-service-architecture.html` §5.5 PE（本仓 `public/dev-standards/architecture/`）](../../public/dev-standards/architecture/backend-service-architecture.html)。**答**：走 Personalization Engine 的 `evalFeature()` 接口，不直连 GrowthBook。SDK 在 [`services/personalization-engine`](https://gitlab.addx.ai/services/personalization-engine)，gRPC（集群内）/ HTTP（跨集群）协议。理由：换 GrowthBook 时业务无感、统一打点。要接入吗？我可以走 B 模式生成代码（`/microservice-integrate personalization`）。

### Good — A 模式（GitLab group 咨询）

**用户**："我新建一个服务叫 referral-program，放哪个 group？"

✅ 我：referral 是 C 端导购转化的业务能力，属于 **C 端业务平台**（按 §2 判定原则：有业务语义、横向能力，不是垂直产品）。按 §1.x GitLab group 命名约定，应建在 **`services/referral-program`**。注意：当前 GitLab 上 `services/value-added/` 已有 `engagement`，referral 跟它边界要先厘清——你确认一下是新独立服务还是 engagement 的子模块？

### Good — B 模式（接入代码）

**用户**：`/microservice-integrate entitlement`

✅ 我：
1. 检测到 Go 项目（go.mod 存在）
2. 读取 `references/entitlement.md`
3. verify.sh 扫描 → 未接入
4. 生成 gRPC client + Kafka consumer + 错误处理 + 幂等逻辑，配置从 config 读取
5. `go build` 通过
6. 跑 verify.sh → 全 PASS，展示报告

### Bad — 混用模式 / 凭记忆答

**用户**："push 通知应该用什么？"

❌ 我（错）："直接用 FCM SDK 就行" —— **凭记忆答**，没查文档，违反铁律
❌ 我（错）：直接生成 FCM client 代码 —— **A 问题用 B 模式答**，跳过咨询直接写代码

✅ 正确：先 Read 架构文档 §6.5 Novu → 答"走 Novu，业务不直连 FCM/APNs。Novu 还在立项中（Local PoC：`/home/jchen/novu-poc`），落地时间问 owner"

---

## 注意事项

- **A 模式答案必须引文档章节** —— "按 §1 调用规则……" / "见 §5.5 PE 备注……"
- **B 模式只生成基础设施代码**（client、consumer、配置），业务逻辑留 TODO
- **不修改平台方代码或配置**
- **不混用模式**：架构问题先 A 答完，明确要写代码再切 B
- **架构文档是 SSOT**——找不到答案时明确告知"文档未覆盖"，不编造
