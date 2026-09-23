---
name: tracking-lifecycle
description: 当用户提到"埋点设计"、"埋点方案"、"tracking design"、"事件抽象"、"注入埋点"、"埋点验证"、"埋点平台"、"埋点管理"，或需要从需求文档产出完整埋点实现时触发。
---

# tracking-lifecycle

从埋点设计到沙盒验证，经过埋点设计、一致性评审、平台创建、代码注入、
本地埋点断言、沙盒验证六个步骤，完成埋点全链路闭环。

本 Skill 已包含埋点平台管理能力（原 tracker-manager），无需额外引用。
引用其他 Skill（superset、datahub、grafana）时，AI 应读取对应 SKILL.md 并遵循其规则执行。

## 描述

适用场景：
- 新功能开发需要添加埋点
- 从已有 US/技术方案中补充埋点
- 埋点方案评审
- 查询/管理已有埋点（平台 API 操作）

不适用场景：
- 仅查看监控数据 → 使用 grafana / superset

## 规则

### Rule 1 — 执行流程

**上游输入**：metric-spec.html（指标定义文档）。指标定义不属于本 Skill 的能力范围，
由用户/产品经理提供或通过其他方式产出。如果用户没有 metric-spec.html，提示先完成指标定义再开始。

**文档格式规则**：指标文档和埋点设计文档的 authoring SSOT 必须是 HTML（`metric-spec.html` / `tracking-design.html`）。事件 YAML、公式、审计表等机器可读内容写在 HTML 内的 `<pre><code class="language-yaml">` 或 `<script type="application/yaml" data-tracking-schema>` 块中，validator 读取这些块；不要为了 validator 另建 `.md` 文档。

Task Progress:
- [ ] Step 0: 现有埋点审计 — 梳理当前应用已有埋点，识别可复用/冲突项
- [ ] Step 1: 埋点设计 — 基于指标文档产出埋点详细文档 tracking-design.html（需人工审核）
- [ ] Step 2: 一致性评审 — 检查两份文档间及文档内的一致性
- [ ] Step 3: 平台创建 — 批量创建埋点配置到埋点平台
- [ ] Step 4: 代码注入 — 按两份文档在各端注入埋点代码
- [ ] Step 5: 本地埋点断言 — L1 mock 验证 + L2 test sink 集成测试
- [ ] Step 6: 沙盒验证 — L3 真实上报沙盒 collector，可纳入 CI 卡控

每步完成后必须提示用户确认 [y/N]，不得跳步。
用户可以从任意 Step 开始（如已有埋点设计，可从 Step 2 开始）。

**下游衔接**：Step 6（沙盒 L3）是开发期天花板。**L4/L5（staging 真实管道 + `/api/release/validate` 通过）是部署后的 gate，开发期无法闭环、无 dev-side 旁路**（详见 Rule 1.6 末「下游边界」）。验证上报必须走真实 SDK 代码路径、禁止手搓模拟（见 Rule 1.6「上报方式」）。平台工单审批/发布走 UI；指标可视化和告警由 superset/grafana skill 负责，不在本 Skill 范围内。

### Rule 1.0 — Step 0: 现有埋点审计

**目标**：在设计新埋点之前，梳理当前应用在平台上已有的全部埋点事件，为 Step 1 提供基线。

**步骤**：

1. 调用平台 API 获取当前应用的所有事件：
   ```
   GET /api/info/search?applicationId={id}
   ```

   **⚠ 响应结构注意**：该 API 返回**嵌套树形结构**，不是扁平列表。
   顶层 `data` 数组只包含 PAGE 和 SELF_DEFINE 类型事件，MODULE 和 COMPONENT 事件
   嵌套在其 parent 的 `children` 数组中。必须**递归遍历** `children` 才能获取全部事件。

   响应结构示意：
   ```json
   {
     "data": [
       {
         "id": 2, "name": "测试页面", "point": "test_page", "type": 1,
         "children": [
           {
             "id": 3, "name": "测试模块", "point": "test_module", "type": 2,
             "children": [
               { "id": 4, "name": "测试组件", "point": "test_comp", "type": 3 }
             ]
           }
         ]
       },
       { "id": 30, "name": "自定义事件", "point": "custom_evt", "type": 4 }
     ]
   }
   ```

   每个事件的关键字段：
   - `id` — 平台 ID
   - `name` — 事件名称
   - `point` — 埋点标识符
   - `type` — 事件类型（1=PAGE, 2=MODULE, 3=COMPONENT, 4=SELF_DEFINE）
   - `children` — 子事件数组（MODULE 在 PAGE 下，COMPONENT 在 MODULE 下）
   - `parameterList` — 参数列表（含 `name`, `valueType`, `isRequired`, `description`）
   - `baseSchemas` — 基础参数 ID 列表

   详细字段定义参见 [references/api-reference.md](references/api-reference.md)。

2. 按 SPM 层级（PAGE → MODULE → COMPONENT → SELF_DEFINE）整理成树形结构，展示：
   - 事件名称、point、类型、tracker_type
   - 参数列表（如有）
   - 所属工单版本（如有）
3. 分析与本次需求的关系：

   | 分类 | 说明 | 处理方式 |
   |------|------|---------|
   | 可复用 | 已有事件的 point/参数完全满足新指标需求 | Step 1 中标记为"复用"，不重复定义 |
   | 可扩展（更新） | 已有事件需要新增参数才能满足新指标 | 标记为"更新"，**记录该事件已有的全部参数**（补参数时不能丢，见 Step 1「变更不可变约束」）；新增参数在 Step 1 定义 |
   | 命名冲突 | 新设计的 point 与已有 point 重名 | Step 1 中必须更换 point 命名 |
   | 无关 | 与本次需求无关的已有事件 | 忽略 |

4. 输出**审计报告**，展示给用户确认

**审计报告格式**：

按 SPM 层级树形展示 parent 归属关系（PAGE → MODULE → COMPONENT），无 parent 的独立事件放顶层：

```
现有埋点审计报告（应用: {应用名}, ID: {id}）

已有事件: {N} 个（PAGE: {n1}, MODULE: {n2}, COMPONENT: {n3}, SELF_DEFINE: {n4}）

SPM 层级树:
├── [PAGE] store_page (ID=10) — 商店页面
│   ├── [MODULE] product_card (ID=11) — 商品卡片
│   │   └── [COMPONENT] buy_btn (ID=12) — 购买按钮
│   │       params: product_id(string), price(float)
│   └── [MODULE] banner (ID=13) — 轮播广告
├── [PAGE] profile_page (ID=20) — 个人中心（无子事件）
└── [SELF_DEFINE] app_launch (ID=30) — 应用启动

与本次需求的关系:
- 可复用: {列出 point、原因、对应指标}
- 可扩展: {列出 point、需要新增的参数}
- 命名冲突: {列出冲突的 point}
- 无关: {N} 个（省略详情）

结论: {是否存在需要特别处理的事项}
```

审计报告确认后 [y/N]，进入 Step 1。

### Rule 1.1 — Step 1: 埋点设计

**输入**：经用户确认的 metric-spec.html

**产物**：`docs/03-detailed-design/{feature}/tracking-design.html`

**文档要求**：

**自动字段排除**：设计参数前，先参见 [references/common-event-schema.md](references/common-event-schema.md) 了解各端 SDK 自动附带的字段（user_id、device_sn、language 等），tracking-design.html 中**不要重复定义**这些自动字段。

对每个埋点事件，必须包含以下内容：

1. **事件描述** — 用自然语言说明这个埋点是什么
2. **触发场景** — 具体在什么用户操作/系统行为下触发（如"用户点击商品详情页的购买按钮时"）
3. **数据示例** — 给出该事件上报的参数示例值（如 `product_id: "SKU_12345", price: 29.99`）
4. **指标来源** — 该埋点定义来源于 metric-spec.html 中的哪些指标
5. **指标参与** — 该埋点参与哪些指标的计算，在公式中扮演什么角色（分子/分母/维度）
6. **计算公式回溯**（必须） — 逐条列出该埋点参与的每个指标的完整计算公式（从 metric-spec.html 原文复制）。该字段是 Step 2 公式一致性检查的前提，缺失则 Step 2 无法执行。当一个事件参与多个指标时，每个指标各占一行，格式为 `指标名 = 公式`。
7. **YAML 事件定义** — 结构化的埋点配置，schema 详见 [references/tracking-schema.md](references/tracking-schema.md)

**变更类需求的不可变约束（设计阶段强制）**：

需求不总是"纯新增"，也可能是**对已有点位的修改**（最常见：给已注册事件补参数）。设计这类变更时，必须**先判断每个点位/参数是"本次新增"还是"更新已有"**，再遵守下面的不可变约束——**在设计阶段就不能提出违反约束的改动**，否则 Step 3 平台创建时会被后端拒绝：

| 对象 | 是否已随**之前已发布(RELEASED)的工单**上线 | 可否修改 |
|------|------|---------|
| 事件 `point` | 是（历史已发布版本里已有） | ❌ point 不可改（后端报 `从之前工单继承的点位不可修改`）；只能新增参数或另建新点位 |
| 参数 `name` / 类型`value_type` | 是（该参数已随已发布工单上线） | ❌ 名称与类型均不可改（后端报 `非当前版本新增的参数的名称与类型不可修改`） |
| 事件 / 参数 | 否（本版本即当前活跃工单内新增、历史已发布版本里没有） | ✅ point / name / 类型 均可自由修改或删除 |
| 任意 | 应用从无已发布工单（首次接入） | ✅ 无约束，全部可改 |

- 判定基准是**之前最近一个已发布(RELEASED)的工单**，不是"任何历史改动"；当前活跃工单内尚未发布的内容仍可改。
- **补参数不得丢失旧参数**：更新走**全量覆盖**（Step 3 用 `saveOrUpdateEventInfo`，服务端先删后写）。因此设计时必须把该事件**已有的全部参数 + 新增参数**一起列出；Step 0 审计时即应记录每个待更新事件的现有参数清单。

**设计阶段还需避免（平台会拒绝，完整清单见 [api-reference.md「平台校验规则与禁止操作」](references/api-reference.md#平台校验规则与禁止操作速查)）**：

- **参数名不要用敏感词**：命中 `password`/`pswd`/`secret`/`token`/`phone`/`mobile`/`email`/`ssid`/`mac`/`ip`/`address`（大小写不敏感、子串匹配）会被拒（`检测到敏感字段`）。PII 应端上脱敏/哈希后用非敏感名上报。**Step 2 的 `tracking-spec-validator.js` 已对此硬卡控（SENSITIVE，ERROR 级）**，设计稿带敏感参数名直接 FAIL。
- **同一事件参数 trackerType 不要混用 base 与 clk/exp**；**同一事件内参数名不要重复**。
- **组件(COMPONENT)的所属模块必须属于同一页面、同一应用**，不能跨页面复用模块。
- 删除已**发布过**的埋点是不允许的（`无法删除已发布过的埋点`）；如需停用走"新建替代点位 + 旧点位保留"的迁移策略。

**一致性保障**：

tracking-design.html 必须保持全文一致性。禁止只修改局部而不检查全文影响。
每次对该文档进行局部修改后，必须运行 validator 做自动化一致性检查：

```bash
node scripts/tracking-spec-validator.js \
  --tracking-design=docs/03-detailed-design/{feature}/tracking-design.html \
  --metric-spec=docs/03-detailed-design/{feature}/metric-spec.html
```

validator 自动覆盖以下 4 项检查：
1. 自然语言描述与 YAML 定义语义匹配（SEMANTIC 规则）
2. 事件间 parent 引用正确性（R4/R5 规则）
3. 跨事件同名参数 value_type 一致性（CONSISTENCY 规则）
4. 计算公式回溯与 metric-spec.html 一致性（FORMULA 规则）

validator 输出 `PASS` 后方可提交人工审核。如有 ERROR/WARNING，修改后重新运行直到通过。

该文档需要人工审核 [y/N]，确认后进入 Step 2。

### Rule 1.1.1 — 埋点覆盖完整性（Coverage Completeness）

**原则**：用户可感知的每一个交互触点都必须有对应埋点，不留盲区。这条规则在 Step 1（设计）和 Step 2（评审）阶段都要检查。

**强制要求**：

| 触点 | 必须的埋点 | 说明 |
|------|-----------|------|
| 页面（路由级屏幕） | PAGE + BASE (PV) | 每个独立页面首次可见时上报 |
| 可点击元素（button / card / tab / menu item / switch / icon button 等） | COMPONENT + CLK | 参数需带能定位元素的业务标识（product_id / position / order_id 等） |
| 首屏关键模块（商品位、广告位、推荐位等） | MODULE + EXP | 进入可视区域时上报，用于归因分析 |

**允许的例外**（必须在 tracking-design.html 中**显式说明**理由）：
- 纯装饰性 UI（logo、分隔线、装饰图）— 无业务价值
- 系统导航（返回、关闭）— 如已有全局 navigation schema 统一覆盖
- debug / 内测面板 — 用户不可见
- 第三方 SDK 内嵌内容（如 WebView）— 不可控

**在 Step 1 的落实方式**：

设计 YAML 事件前，先在 tracking-design.html 顶部产出"交互触点清单"表，逐项标注是否加埋点：

| 触点类型 | 触点描述 | 是否加埋点 | 对应事件 point / 跳过原因 |
|---------|---------|-----------|-------------------------|
| PAGE | 商店首页 | ✓ | `store_page` |
| COMPONENT | 商品卡"加入购物车"按钮 | ✓ | `add_to_cart_btn` |
| COMPONENT | 页眉 logo | ✗ | 纯装饰性 UI |
| MODULE | 首页 banner 轮播 | ✓ | `home_banner` |

清单覆盖所有交互触点后，再进入 YAML 事件定义。

**在 Step 2 的落实方式**：

- **自动化**：`tracking-spec-validator.js` 包含 `INTERACTION_COVERAGE` 规则（WARNING 级别）作为软检查：
  - 存在 MODULE/COMPONENT 事件但缺 PAGE 事件 → 提示可能漏了 PV
  - 存在前端事件（PAGE/MODULE/COMPONENT）但无任何 CLK 事件 → 提示可能漏了用户行为埋点
  - 软检查只是**提醒**，不阻断流程；真正的完整性判断仍需人工对照交互触点清单
- **人工补充**：对照 Step 1 的"交互触点清单"逐项核对，确认清单中每一项要么有埋点，要么有显式例外理由。

### Rule 1.2 — Step 2: 一致性评审

**目标**：找出 metric-spec.html 和 tracking-design.html 之间以及各自内部的不一致。

本步骤只关注一致性，不做合理性判断或优化建议。

#### 执行方式：先自动化，再人工补充

**第一步 — 运行 tracking-spec-validator.js**：

```bash
node scripts/tracking-spec-validator.js \
  --tracking-design=docs/03-detailed-design/{feature}/tracking-design.html \
  --metric-spec=docs/03-detailed-design/{feature}/metric-spec.html
```

validator 自动覆盖以下检查维度（无需人工重复检查）：

| 自动检查项 | validator 规则 | 级别 |
|-----------|---------------|------|
| YAML 格式合规 | type/tracker_type/point/parent 等 | ERROR |
| point 唯一性 | R3 | ERROR |
| 事件内参数名唯一 | YAML | ERROR |
| **敏感字段参数名**（password/phone/email/ip/mac/address 等，命中即拒，镜像平台规则） | SENSITIVE | ERROR |
| parent 引用正确性 | R4/R5 | ERROR |
| 跨事件参数类型一致性 | CONSISTENCY | WARNING |
| 描述↔YAML 语义匹配 | SEMANTIC | WARNING |
| 自然语言 7 项完整性 | NL_COMPLETENESS | ERROR/WARNING |
| 指标覆盖（所需事件→YAML） | COVERAGE | ERROR |
| 事件来源（指标来源标注） | SOURCE | WARNING |
| 公式回溯一致性 | FORMULA | ERROR |
| 告警字段完整性 | ALERT | WARNING |
| 交互触点覆盖（Rule 1.1.1 软检查） | INTERACTION_COVERAGE | WARNING |

**第二步 — 人工补充检查**（validator 无法覆盖的维度）：

| 人工检查项 | 说明 |
|-----------|------|
| 参数充分性 | metric-spec.html 公式所需的维度/参数，在 tracking-design.html 的事件参数中是否都已定义 |
| metric-spec.html 内部 | 同一事件在不同指标的"所需事件"列中名称是否一致 |

#### 结果判定

- validator 输出 `result: "PASS"` 且人工检查无问题 → 评审通过
- validator 输出 `result: "FAIL"` 或 `result: "WARN"` → 根据错误/警告逐条修复

**不一致修复流程**：

如发现不一致，按以下规则回退修复，修复后重新执行 Step 2（重新运行 validator），直到清单为空：

| 问题来源 | 修复方式 |
|----------|---------|
| metric-spec.html 有误 | 该文档受不可变性约束，必须向用户说明问题并获得明确授权后才能修改，修改后重新执行 Step 1 → Step 2 |
| tracking-design.html 有误 | 修改后必须启动 Agent 全文 review 一致性（Rule 1.1 一致性保障），再重新执行 Step 2 |
| 两份文档都有误 | 先修复 metric-spec.html（需用户授权），再更新 tracking-design.html，再重新执行 Step 2 |

**输出示例**：

```
一致性评审结果：

[自动化检查] tracking-spec-validator 输出：PASS（0 errors, 0 warnings）

[人工补充检查] 发现 1 项不一致：

1. [参数充分性] metric-spec.html 指标"商店页购买转化率"按"去重用户数"统计，
   但 tracking-design.html 的购买按钮点击事件缺少 user_id 参数支撑去重。
   → 建议：确认 user_id 是否通过 base_schemas 自动注入，如未注入则需显式添加。
```

一致性评审通过（validator PASS + 人工检查无问题）后，展示结果给用户确认 [y/N]，确认后进入 Step 3。

### Rule 1.3 — Step 3: 平台创建

**前提**：Step 2 一致性评审通过。

**认证方式**：

平台 API 支持两种 Token 认证，均通过 `Authorization: Bearer <token>` 方式携带：

| Token 类型 | 前缀 | 权限 | 有效期 | 创建方式 |
|-----------|------|------|--------|---------|
| Personal Access Token (PAT) | `tmt_` | 继承创建者角色（读写） | 7/14/30 天 | 任意登录用户在平台 UI「个人设置 → Access Token」页面创建 |
| Project Access Token | `tmp_` | 只读，可选绑定应用 | 30/90/180 天 | Admin 在平台 UI「项目设置 → Project Token」页面创建 |

**如何获取 Token：**

1. **PAT（推荐，用于埋点创建等写操作）**：
   - 登录埋点管理平台 → 左侧菜单「Access Token」→ 点击「创建」
   - 填写名称、选择有效期（建议 7 天）→ 创建后**立即复制保存** token（仅显示一次）
   - 存入环境变量：`export TMT_TOKEN="tmt_xxx"`

2. **Project Token（只读场景，如其他系统集成查询）**：
   - 需 Admin 权限。登录平台 → 左侧菜单「Project Token」→ 点击「创建」
   - 可选绑定特定应用（绑定后仅能访问该应用数据）
   - 存入环境变量：`export TMT_TOKEN="tmp_xxx"`

**注意**：Token 不能用于签发新 Token，创建 Token 必须通过账号登录会话操作。

**前提**：

1. **应用必须有活跃工单**（releaseStatus ∈ {0 待审核, 1 待审批, 2 已审核}，即 ≠ 3「已发布」且 ≠ 4「已废弃」），否则报「请先创建工单」。
   可通过 `GET /api/release/getAllRelease?applicationId={id}` 检查，无活跃工单时需先在平台创建。
   - 工单状态语义见 [api-reference.md「工单状态」](references/api-reference.md#工单状态releasestatus)。**平台没有"草稿态"**；status=0 是新建工单的初始态「待审核」，0/1/2 三个进行中状态对事件创建**行为完全一致**（后端只按 ≠3 ≠4 判活跃，不区分具体值），不存在"某个进行中状态能建、另一个不能建"。
2. **MODULE / COMPONENT 必须正确引用父节点**：`parentPage`（及 COMPONENT 的 `parentModule`）要指向**真实存在**的 PAGE / MODULE。
   - 缺失时报明确错误（`页面id不能为空` / `无法解析 parent point`），不是 NPE。
   - **SELF_DEFINE 与 PAGE 无层级依赖**：无需 `parentPage`/`parentModule`，也不要求先建 PAGE（但和所有事件一样仍归属某应用，由批次顶层 `applicationId` 指定）。
   - ⚠️ 已知平台缺陷：单事件直连接口 `saveOrUpdateEventInfo` 若传入"指向已删除/不存在页面"的 `parentPageId`（非空但失效），会裸 NPE（`{"code":500,"errorMessage":null}`）；**批量接口 `batchCreateEvents` 已做 parent 存在性解析，不受影响**——优先用批量接口。

**步骤**：

1. 读取 tracking-design.html 中的 YAML 事件定义
2. 调用平台 API 查询已有事件（`GET /api/info/search?applicationId={id}`），与 YAML 比对：
   - 已存在且未修改 → 跳过
   - **已存在但有变更（如漏写参数、需补 optional 字段），且点位在当前活跃工单内（未发布）** →
     用 `POST /api/info/saveOrUpdateEventInfo`（带事件 `id`）**原地编辑**，可纯 API 闭环，无需 UI。
     先 `GET /api/info/getEventDetail?eventId={id}` 取回完整定义，append 新参数后整体回传
     （服务端全量覆盖参数列表，必须带上已有参数）。详见 [references/api-reference.md](references/api-reference.md)。
     **提交前必须跑防丢参数卡控**（POST 前硬卡，漏带已有参数会被服务端静默删除）：
     ```bash
     node scripts/check-event-update.js --current=<getEventDetail响应.json> --payload=<待提交payload.json>
     ```
     输出 `result: "FAIL"`（列出会被删除的参数 / id 不一致）就**不要提交**，补全参数列表后重跑至 PASS。
   - 不存在 → 新建（`POST /api/info/batchCreateEvents`）
   - 注意：已随**之前已发布工单**上线的事件 `point`、参数 `name`/类型**不可修改**（设计阶段就应遵守，见 Step 1「变更类需求的不可变约束」）；如需改 point 必须新建事件升版。本版本新增、尚未发布的点位/参数仍可改
3. 列出操作清单（新建/升版/跳过），展示给用户确认
4. 使用 `scripts/yaml-to-batch-payload.js` 将 YAML 转换为 API 请求体，再调用 `POST /api/info/batchCreateEvents` 批量创建：
   ```bash
   node scripts/yaml-to-batch-payload.js --input=tracking-design.html --app-id={id} --execute
   ```
   - 脚本自动完成 snake_case → camelCase 字段映射、parent point 引用、参数校验
   - 服务端自动按层级排序（PAGE → MODULE → COMPONENT）并解析 parent point → ID
   - 同一事务，任一失败全部回滚
5. 创建完成后展示结果清单（point → 平台 ID），等待用户确认

**失败处理**：

| 结果 | 处理 |
|------|------|
| 全部成功 | 进入 Step 4 |
| 部分失败（子级失败） | 检查父级是否创建成功，修复后重试失败项 |
| 部分失败（权限/网络） | 展示错误详情，提示用户检查 token 或网络后重试 |
| 返回 `{"code":500,"errorMessage":null}`（裸 NPE） | 后端 NullPointerException。优先用**批量接口** `batchCreateEvents`（已对 parent 做存在性解析，规避了单事件接口"失效 parentPageId"的裸 NPE）。若仍出现，附 applicationId + 出错 point + 上报时间反馈平台团队，便于按服务端日志（含 `stack_trace` 字段）定位 |
| 全部失败 | 阻断，不进入 Step 4 |

### Rule 1.4 — Step 4: 代码注入

**输入**：metric-spec.html + tracking-design.html

**前置检查**：调用平台 API（`GET /api/info/search?applicationId={id}`，参见 [references/api-reference.md](references/api-reference.md)）
获取当前应用的事件列表，与 tracking-design.html 比对，确认所有事件都已在平台创建成功。如有缺失，回到 Step 3。

**各端实现规范**：参见 [references/platform-impl-spec.md](references/platform-impl-spec.md)
（该文档由用户维护，如文档不存在或内容为空，提示用户补充后再继续）

**SDK API 签名**：参见 [references/sdk-api.md](references/sdk-api.md)（含各端完整方法签名、接口定义和配置项）

**代码注入模板**：参见 [references/injection-templates/](references/injection-templates/) 目录下的平台模板文件，
按 `{{point}}`、`{{page}}`、`{{module}}`、`{{params}}` 等占位符填充后注入。

**平台类型识别**：

| 项目特征 | 平台类型 | 参考规范 |
|----------|---------|---------|
| pubspec.yaml 存在 | Flutter | platform-impl-spec.md Flutter 部分 |
| build.gradle / AndroidManifest.xml 存在 | Android 原生 | platform-impl-spec.md Android 部分 |
| .xcodeproj / Package.swift 存在 | iOS 原生 | platform-impl-spec.md iOS 部分 |
| pom.xml / build.gradle (Spring Boot) 存在 | 后端 | platform-impl-spec.md 后端部分 |

如果项目包含多个平台（如 Flutter + Android 原生），逐个平台注入，每个平台单独展示 diff。

**步骤**：

1. 读取 tracking-design.html 中的事件定义和触发场景
2. 识别当前项目的平台类型（见上表），加载对应的平台实现规范
3. 根据触发场景确定注入位置
4. 生成完整 SDK 调用代码并注入
5. 注入原则：只做 additive 修改，保持代码风格一致，每个文件展示 diff
6. 注入完成后运行项目编译/lint 检查（Flutter: `flutter analyze`，Android: `./gradlew lint`，iOS: `xcodebuild`，后端: `mvn compile`），确保注入代码不破坏构建
7. 如果编译/lint 失败，修复错误后重新展示 diff

**平台未识别处理**：如果项目不匹配上表任何特征，提示用户指定平台类型和对应的 SDK 调用方式后再继续。

**验收标准**：用户确认所有 diff 无误 [y/N]，且 tracking-spec-validator.js 校验通过。

### Rule 1.5 — Step 5: 本地埋点断言

**目标**：在本地环境（无网络）验证埋点调用逻辑和事件数据结构正确。
这是验证重心所在，应在此层发现绝大多数埋点问题。

**验证分层策略**：参见 [references/verification-strategy.md](references/verification-strategy.md)

**测试方案**：参见 [references/testing-guide.md](references/testing-guide.md)

#### L1 — 单元测试（mock SDK）

对每个埋点事件编写单测，mock SDK 接口，verify 调用参数：

1. 从 tracking-design.html 提取事件清单
2. 对每个事件写至少一个单测：mock SDK → 触发业务动作 → verify 事件名、参数、调用次数
3. 运行测试确保全部通过

**断言覆盖**：
- 事件名 / point 正确
- 调用参数（字段名 + 值）与 tracking-design.html 一致
- 调用次数符合预期（如去重逻辑）

#### L2 — 集成测试（test sink）

对有 UI 交互的端（Flutter、Android、iOS），注入 test sink 捕获完整事件列表：

1. 注入 test sink 替换真实 reporter
2. 编写集成 / Widget 测试：渲染页面 → 模拟用户操作 → 从 test sink 读取事件
3. 对事件做结构化断言：事件名、payload 字段与值、触发次数、（可选）事件顺序
4. 运行测试确保全部通过

**后端不需要 L2**：后端只有 SELF_DEFINE 事件，无 UI 交互链路，L1 已足够。

#### 通过标准

- L1：tracking-design.html 中的每个事件至少有一个通过的单测
- L2：有 UI 交互的端，关键用户流程的集成测试通过，事件结构化断言全部 PASS

展示测试结果给用户确认 [y/N]，确认后进入 Step 6。

### Rule 1.6 — Step 6: 沙盒验证

**目标**：验证真实 SDK 上报到 collector 的格式和 iglu schema 均正确。
沙盒 collector 实时可查，适合纳入 CI 自动化卡控。

**前提**：
- Step 5 本地埋点断言通过
- Step 3 中事件已在平台创建成功（创建时 schema 自动注册到 micro/staging S3，无需等工单发布）

**沙盒 Collector URL**：`https://us-prod-log-sandbox.theunismart.com`
- 上报端点：客户端/后端 SDK 初始化时将 collector URL 指向此地址
- 查询接口：`/micro/good`（验证通过的事件）、`/micro/bad`（验证失败的事件）

**Namespace 约定**：沙盒 namespace 仅用作过滤条件，格式 `test-{应用名}`，如 `test-vicohome-android`、`test-iot-service`。
- **App 端**：在埋点平台 `/check/dashboard/` 页面用该 namespace 生成配置，扫码配置到 App
- **后端 / 非 App 端**：在测试代码或 SDK 初始化时直接写入 namespace

#### iglu Schema 路径规则

沙盒验证的核心是 iglu schema 路径必须与平台注册的一致。路径由 `SchemaFileUtil.createSchemaFilePath` 生成，规则如下：

**URI 格式**：`iglu:com.{appPoint}/{name}/jsonschema/{releaseVersion}`

| 事件类型 | name 生成规则 |
|---------|-------------|
| PAGE (1) | `{point}_pv` |
| MODULE (2) | `{page.point}_{point}_{trackerType}` |
| COMPONENT (3) | `{page.point}_{module.point}_{point}_{trackerType}` |
| SELF_DEFINE (4) | `{point}` |

- `trackerType` 名称：`base` / `clk` / `exp`
- MODULE 和 COMPONENT 会为所有非 base trackerType（clk、exp）各生成一个 schema，不论事件本身的 trackerType
- ⚠️ **`releaseVersion` 必须取自活跃工单的 version 字段（如 `1-0-9`，通过 `GET /api/release/getAllRelease?applicationId={id}` 查询），不要默认写 `1-0-0`。** 事件注册时的 iglu schema 版本 = 注册当时活跃工单的 version；上报端（尤其是手写/非公司 SDK 的上报）若用错版本（如工单是 `1-0-1` 却上报 `1-0-0`），iglu resolver 会 NotFound，事件落入 `dwd_bad_events`（沙盒 snowplow-micro 表现为 `ResolutionError NotFound`）

**示例**：应用 `test_app`，活跃工单 version=`1-0-9`

| 事件 | 类型 | iglu schema |
|------|------|------------|
| store_page | PAGE | `iglu:com.test_app/store_page_pv/jsonschema/1-0-9` |
| product_card（parent: store_page） | MODULE | `iglu:com.test_app/store_page_product_card_exp/jsonschema/1-0-9` |
| buy_btn（parent: detail_page > product_info） | COMPONENT | `iglu:com.test_app/detail_page_product_info_buy_btn_clk/jsonschema/1-0-9` |

#### 上报方式（优先级铁律：有 SDK 走真实代码，无 SDK 才用脚本兜底）

**按是否有真实埋点 SDK 集成分两条路，不可混用：**

1. **有真实埋点 SDK 集成 →（首选）必须走真实业务代码路径触发上报**：
   - **App 端**：真机/模拟器跑**真实代码**、做真实操作（真实点击、真实页面进入）触发埋点，collector 指向沙盒地址。
   - **后端 / Node 端**：在**真实服务代码或集成测试**里经 SDK 上报。
   - **不要**因为图省事就跳过真实代码、另写一个手攒事件的脚本——那样验不到真实集成（业务代码埋点是否真触发、SDK 配置/context/自动字段是否正确）。
2. **没有 SDK 集成 / 跑不了真实代码路径 →（兜底）才用脚本**：按下方「数据准备 + 验证步骤」生成 `scripts/sandbox-upload.js`，用 `@snowplow/node-tracker`（**SDK，非原始 HTTP**）发沙盒。此路径只验 schema 格式，不证明真实代码能上报对。

**两条路都严禁**：

- **绕过 SDK 手搓原始 HTTP POST / 直发 collector `/tp2`**——绕过 SDK = 验不到真实集成、数据通路也不对。
- **在开发期用任何手段（override / harness / 直发）想让 L4/L5（`/api/release/validate`）通过**——它是部署后 gate、无 dev 旁路（见本 Rule 末「下游边界」）。

> ⚠️ 反面案例（issue #18）：有人**有 SDK 却绕过它**，用 Node 脚本直发 test collector + 强制 `currentEnvironment=staging` override + harness 一次性灌 37 个事件想过 L5，耗约 4h 全部走不通。两个错：绕过真实 SDK 代码路径、且想在 dev 期硬过部署后才有的 L4/L5。

兜底脚本（无 SDK 时）参考 `tracker_manager_frontend/tests/enriched.js` 的模式：

```javascript
const { newTracker, buildSelfDescribingEvent } = require('@snowplow/node-tracker');

const t = newTracker(
  { namespace: 'test-{应用名}', appId: '{appPoint}', encodeBase64: false },
  { endpoint: 'https://us-prod-log-sandbox.theunismart.com', bufferSize: 1 }
);

t.track(buildSelfDescribingEvent({
  event: {
    schema: 'iglu:com.{appPoint}/{name}/jsonschema/{version}',  // {version} = 活跃工单 version，切勿默认 1-0-0
    data: { /* 事件参数 + 绑定的 base schema 参数 */ }
  }
}), contexts);  // contexts 格式见下方说明
```

#### Context（base-schema）确定方式

**不要硬编码 context 字段。** 不同端/应用的 base-schema context 不同，必须参考**实际客户端 SDK 代码**确定：

| 端 | base-schema iglu URI | 字段来源 |
|---|---------------------|---------|
| Android / iOS（App） | `iglu:com.base/base-schema/jsonschema/1-0-1`（硬编码） | SDK 中的 `baseParams()` 方法，固定字段：user_id, device_sn, language, push_permissions, countryNo, tenantId, build_env, is_open_vpn, is_used_proxy |
| 后端 Java SDK | `iglu:com.{appId}/{baseSchema}/jsonschema/{baseSchemaVersion}`（配置生成） | SDK 初始化配置 + `getBaseContext()` 方法，字段为 spm, type + 调用方传入的 context map |
| IoT 设备 | `iglu:com.{appId}/base-schema/jsonschema/{version}`（per-app） | 设备端 SDK，字段包含 sn, model, firmware_type 等设备信息 |

**确定步骤**：

1. **找到目标项目的 SDK 初始化代码**，确认使用的 base-schema URI 和 version
2. **找到 context 组装代码**（如 Android 的 `baseParams()`、Java SDK 的 `getBaseContext()`），列出所有字段及类型
3. **构造沙盒 context 数据**：string 类型字段传空字符串或测试值，boolean 字段传 `false`，number 字段传 `0`
4. **特别注意**：`user_id` 传**空字符串**，否则沙盒 JS enrichment 会尝试解析 user token 报错

如果目标项目**尚未集成 SDK**（如新项目），参考同类应用的已有实现来确定 context 格式。

#### Context schema 不满足时的处理

沙盒验证时 context 的 iglu schema 必须已注册到 iglu registry，否则会报 `schema_violations`。不同场景的处理方式：

| 场景 | context schema 来源 | 处理方式 |
|------|---------------------|---------|
| App 端（Android/iOS） | 共享 schema `iglu:com.base/base-schema/jsonschema/1-0-x`，由平台全局管理 | 该 schema 始终可用，直接使用即可。如需新增字段，联系平台管理员更新共享 schema 并发布新版本 |
| 后端 Java SDK | 按应用生成 `iglu:com.{appId}/{baseSchema}/jsonschema/{version}`，由平台 baseSchema 配置驱动 | 如果 SDK 发送的字段不在平台 baseSchema 中，需先在平台 UI `/schema/list/` 更新 baseSchema 参数列表，平台会自动生成新版 iglu schema 并注册 |
| IoT 设备 | 按应用生成，类似后端 SDK | 同上 |
| 沙盒测试脚本（Node.js） | 取决于模拟的客户端类型 | 模拟 App 端用 `iglu:com.base/base-schema/jsonschema/1-0-0`（`additionalProperties: true`，字段宽松）；模拟后端用对应应用的 per-app schema（`additionalProperties: false`，字段严格匹配） |

**关键原则**：context iglu schema 的管理权在**平台端**，SDK 仅引用 URI。当 context 字段需求变化时，修改平台 baseSchema 配置 → 平台自动生成新 iglu schema → SDK 更新配置中的 version 号即可。**不要在 SDK 侧硬编码 schema 内容**。

#### 平台 baseSchema 与 iglu context 的区别

两者容易混淆但用途完全不同：

| | 平台 baseSchema（`/api/baseSchema/list`） | iglu context base-schema |
|---|------------------------------------------|--------------------------|
| **作用** | 参数继承 — 绑定到事件后，参数合并进事件的 iglu schema | 上报封装 — SDK 运行时自动附带的公共字段 |
| **体现位置** | 事件 schema 的 `properties` 和 `required` 中 | Snowplow 事件的 `contexts` 数组中 |
| **影响** | 决定事件 `data` 中哪些字段是必填的 | 决定 context `data` 中需要携带哪些字段 |

**因此**：数据准备阶段查到的 `baseSchemas` 绑定关系，影响的是**事件 data 字段**（必填校验），而不是 context 格式。

#### 数据准备（先查平台，再构造上报数据）

生成上报脚本前，**必须先从平台 API 查询实际的事件定义和 context 参数**，不要凭 tracking-design.html 硬编码：

1. **查询活跃工单 version**：
   ```
   GET /api/release/getAllRelease?applicationId={id}
   ```
   找 releaseStatus ≠ 3 且 ≠ 4 的工单，取其 `version` 字段作为 iglu schema version。
   ⚠️ **必须用这个 version，不能默认 `1-0-0`**：事件 schema 版本随工单 version 走（工单 `1-0-1` → schema 即 `1-0-1`）。上报端用错版本会进 `dwd_bad_events`（snowplow-micro：`1-0-0` → `ResolutionError NotFound`，`1-0-1` → good）。

2. **查询事件树及参数**：
   ```
   GET /api/info/search?applicationId={id}
   ```
   递归遍历树形结构，提取每个事件的：
   - `point`、`type`、`parentId`（用于构造 iglu 路径）
   - `parameterList` — 事件的自定义参数列表（字段名、类型、是否必填），用于构造 `data` 字段
   - `baseSchemas` — 绑定的 base schema ID 列表

3. **查询 base schema 参数**：
   ```
   GET /api/baseSchema/list?applicationId={id}
   ```
   根据事件绑定的 `baseSchemas`，获取 context 中需要携带的参数（字段名、类型、是否必填）。
   这些参数决定了上报时 context data 里除 `spm`、`type` 等固定字段外还需要哪些字段。

4. **构造上报数据**：
   - **event data**：按 `parameterList` 中的字段名和类型构造测试值（required 字段必须包含）
   - **context data**：按 base schema 参数构造，`user_id` 等 string 字段传空字符串，boolean 字段传 `false`
   - **iglu 路径**：按上述路径规则 + 活跃工单 version 拼接

#### 验证步骤

1. **触发上报**：
   - **有 SDK（首选）**：跑真实代码/集成测试触发埋点上报（见「上报方式」），**跳过下面的脚本生成**。
   - **无 SDK（兜底）**：按数据准备结果生成 `scripts/sandbox-upload.js`，再 `node scripts/sandbox-upload.js`。
3. **等待 3-5 秒后查询结果**：
   - `curl https://us-prod-log-sandbox.theunismart.com/micro/good` → 过滤 namespace 匹配的事件
   - `curl https://us-prod-log-sandbox.theunismart.com/micro/bad` → 检查是否有失败事件

#### 验证结果处理

| 结果 | 处理 |
|------|------|
| good 且字段匹配 | 该事件验证通过 |
| bad + `schema_violations` + `ResolutionError` | iglu 路径错误 → 检查路径规则（PAGE 是否加了 `_pv`？MODULE/COMPONENT 是否拼接了 parent point？version 是否正确？） |
| bad + `enrichment_failures` + `invalid token` | `user_id` 非空 → 改为空字符串 |
| bad + `ValidationError` + `additionalProperties` | 上报数据字段不在 schema 定义中 → 检查参数名是否与平台一致 |
| 事件未出现 | 确认 SDK endpoint、namespace、网络连通性 |

更多错误排查参见 [references/troubleshooting.md](references/troubleshooting.md)。

**通过标准**：tracking-design.html 中定义的所有事件全部在 `/micro/good` 中出现且字段匹配。

**完成交接**：Step 6 通过后，AI 提示用户：
> "沙盒验证（L3）已全部通过——这是**开发期的天花板**。L4/L5（staging 真实管道 + 平台 `/api/release/validate` 通过）是**部署后的 gate**，无法在开发期绕过，需代码合并 + staging 部署后再做。"

**下游边界（部署后）：L4/L5 不在开发期范围**

L4/L5（staging 真实管道 + 平台 `/api/release/validate` 通过）是**部署后**的 gate，**开发期无法闭环、也没有 dev-side 旁路**：

- `/api/release/validate` 只是"读 `event_version_status` 表 + 据此推进工单状态"，而该表**只由真实 Snowplow 管道喂、沙盒 snowplow-micro 不写**。所以开发期/沙盒怎么发都 `notUploaded`——没有 `manualValidate`/`triggerValidate` 之类接口能让 dev 绕过部署直接置通过。
- 正确做法：代码合并 → staging 部署 → **用真实代码触发真实 SDK 上报**（不是手搓请求，见 Step 6「上报方式」）→ 等同步 → 平台/发布流自行 validate。工单审批/发布走 UI（Rule 5.3）。
- **沙盒（L3）通过就是开发期天花板**，到此即可交接，不要再去试 L4/L5。

### Rule 2 — 跨会话进度持久化

每完成一个 Step，将进度写入 `docs/03-detailed-design/{feature}/.tracking-progress.json`：

```json
{
  "feature": "store-purchase",
  "current_step": 3,
  "completed_steps": [0, 1, 2],
  "step_results": {
    "0": { "status": "confirmed", "timestamp": "2026-04-07T09:30:00Z", "existing_events": 8 },
    "1": { "status": "confirmed", "timestamp": "2026-04-07T10:00:00Z" },
    "2": { "status": "confirmed", "timestamp": "2026-04-07T11:00:00Z", "validator": "PASS" }
  },
  "application": "vicohome",
  "application_id": 14
}
```

**恢复规则**：
- 新会话启动时，检查 `.tracking-progress.json` 是否存在
- 如存在，读取 `current_step`，提示用户：`"检测到上次进度：Step {N} 已完成，是否从 Step {N+1} 继续？[y/N]"`
- 用户确认后从对应 Step 继续，无需重新执行已完成的步骤
- 进度文件不应提交到 git（加入 .gitignore）

### Rule 3 — 项目自动识别

启动时自动检测当前项目对应的埋点平台 Application：

| 项目特征 | Application |
|----------|-------------|
| pubspec.yaml 含 vicohome | vicohome (ID=14) |
| pubspec.yaml 含 flutter_home | FAPP (ID=100) |
| pom.xml 含 iot-service | IoT Service (ID=527) |
| 其他 | 调用 GET /api/info/getAllApplication 获取列表，提示用户选择 |

切换项目时必须重新识别 Application。

### Rule 4 — 文档管理规范

**文件位置**：
- 指标文档：`docs/03-detailed-design/{feature}/metric-spec.html`
- 埋点文档：`docs/03-detailed-design/{feature}/tracking-design.html`
- 各端实现规范：`references/platform-impl-spec.md`（用户维护）

**文档修改约束**：

| 文档 | 修改触发条件 | 修改后必须 |
|------|------------|-----------|
| metric-spec.html | 仅用户明确要求或 US 变更 | 重新执行 Step 1 → Step 2 |
| tracking-design.html | 仅用户审核要求修改 | 启动 Agent 全文 review 一致性，再重新执行 Step 2 |

**YAML block 标记**：在 HTML 中使用 `<script type="application/yaml" data-tracking-spec>...</script>` 或 `<pre><code class="language-yaml" data-tracking-spec>...</code></pre>`，不要写成独立 Markdown fenced block。

### Rule 5 — 平台交互规则

本 Skill 包含完整的埋点平台管理能力。**API 优先，UI 兜底**。

#### 5.1 认证

优先使用 Personal Access Token（PAT）：`Authorization: Bearer tmt_xxx`。
Token 获取方式见 Step 3 的认证方式说明。

#### 5.2 API 优先策略

| 操作 | API | 说明 |
|------|-----|------|
| 查询应用列表 | `GET /api/info/getAllApplication` | |
| 查询事件树 | `GET /api/info/search?applicationId={id}` | 返回嵌套树形结构 |
| 查询事件参数 | `GET /api/info/getParametersByEventId?id={eventId}` | |
| 批量创建事件 | `POST /api/info/batchCreateEvents` | 需活跃工单 |
| 查询工单列表 | `GET /api/release/getAllRelease?applicationId={id}` | |
| 创建工单 | `POST /api/release/addRelease` | |
| 查询发布状态 | `GET /api/release/getPublishedEvents?application={name}&version={ver}` | |
| 查询 Base Schema | `GET /api/baseSchema/list?applicationId={id}` | |
| Base Schema 详情 | `GET /api/baseSchema/detail?id={id}` | |
| 新增 Base Schema | `POST /api/baseSchema/add` | |
| 修改 Base Schema | `POST /api/baseSchema/update` | |
| 查询 Context 列表 | `GET /api/context/list?applicationId={id}` | |
| Context 详情 | `GET /api/context/detail?id={id}` | |
| Context Schema | `GET /api/context/schema?id={id}` | |
| 保存 Context | `POST /api/context/save` | |

#### 5.3 浏览器兜底

当 API 无法完成操作（如需要审批流、扫码、或 API 报错降级）时，引导用户在平台 UI 操作。

**平台地址**：`https://us-analytics-management.theunismart.com`

**主要页面**：

| 页面 | URL | 适用场景 |
|------|-----|---------|
| 事件管理 | `/spm/list/` | 事件 CRUD、编辑参数 |
| Base Schema | `/schema/list/` | 管理基础参数（新增字段、修改类型） |
| Context 配置 | `/context/list/` | Context 参数管理 |
| 埋点验证 | `/check/dashboard/` | 沙盒/staging/生产验证、扫码配置 |

**必须走 UI 的场景**：

| 场景 | 原因 |
|------|------|
| 工单审批发布（`onlineRelease`） | 需要审批流确认 |
| 沙盒验证扫码配置 | 需要 App 扫码绑定 namespace |
| 刷新验证状态 | 需要实时查看 good/bad 分布 |

**兜底流程**：当 API 调用失败或操作不在 API 覆盖范围时：
1. 告知用户需要在 UI 操作
2. 给出具体页面 URL 和操作步骤
3. 等待用户确认完成后继续下一步

## 示例

### 指标定义文档（上游输入）示例

#### Bad — metric-spec.html

| 指标名称 | 业务含义 | 计算公式 | 重要程度 | 所需事件 | 数据消费方 | 告警设置 |
|----------|---------|---------|---------|---------|-----------|---------|
| CVR | 转化率 | 转化相关事件 | 高 | 购买等 | 产品 | 无 |
| 页面UV | 看页面的人 | 页面访问量 | 中 | 页面事件 | 运营 | 有 |

问题：
1. "CVR" — 使用缩写，未展开业务含义，不同人理解不同
2. "转化相关事件" — 计算公式含糊，未说明分子分母
3. "购买等" — "等"字模糊，无法判断需要哪些事件
4. "高/中" — 未按 P0/P1/P2 分级
5. "页面访问量" — 未说明按人数还是次数统计
6. 告警设置只写"有/无"，没有具体阈值和条件

#### Good — metric-spec.html

| 指标名称 | 业务含义 | 计算公式 | 重要程度 | 所需事件 | 数据消费方 | 告警设置 |
|----------|---------|---------|---------|---------|-----------|---------|
| 商店页购买转化率 | 衡量用户从进入商店页到完成购买的意愿强度。上升说明商品展示和定价有效，下降需排查商品信息或支付流程 | 在商店页点击购买按钮的去重用户数 / 进入商店页的去重用户数 × 100% | P0 — 核心 | 商店页浏览（store_page PV）、购买按钮点击（buy_btn CLK） | 产品经理、增长团队 | 见告警子表 |
| 购买流程各步骤流失率 | 定位购买漏斗中流失最严重的环节。某步骤流失率上升意味着该环节体验恶化 | (上一步去重用户数 - 当前步骤去重用户数) / 上一步去重用户数 × 100%，按步骤分别计算 | P1 — 重要 | 商品详情页浏览、点击购买、确认订单页浏览、支付成功 | 产品经理 | 见告警子表 |
| 商品详情页平均停留时长 | 反映用户对商品信息的阅读深度 | 停留时长总和 / 访问次数 | P2 — 辅助 | 详情页进入时间、详情页离开时间 | 运营 | 见告警子表 |

**告警子表**

| 指标名称 | 是否告警 | 告警条件 | 告警级别 | 通知方式 | 阈值依据 |
|----------|---------|---------|---------|---------|---------|
| 商店页购买转化率 | 是 | 转化率低于 5% 持续 30 分钟 | 警告 | 飞书增长群 | 历史均值 8%，5% 为下限 |
| 购买流程各步骤流失率 | 是 | 任一步骤流失率超过 80% | 通知 | 飞书产品群 | 业务目标 |
| 商品详情页平均停留时长 | 否 | — | — | — | — |

---

### Step 1 埋点设计示例

#### Bad — tracking-design.html

```html
<script type="application/yaml" data-tracking-spec>
application: vicohome
events:
  - name: 购买
    type: PAGE
    tracker_type: CLK
    point: Buy
    parameters: []
</script>
```

问题：
1. 无触发场景描述、无数据示例
2. 未说明来源哪个指标、参与哪个计算
3. type/tracker_type 矛盾
4. point 命名不规范
5. 缺少参数

#### Good — tracking-design.html

**事件：商店页面浏览**

- 触发场景：用户进入商店页面时自动触发
- 数据示例：`source: "home_banner"`
- 指标来源：商店页购买转化率（metric-spec.html）
- 指标参与：作为"商店页购买转化率"的分母（进入商店页的去重用户数）
- 计算公式回溯：商店页购买转化率 = 在商店页点击购买按钮的去重用户数 / 进入商店页的去重用户数 × 100%

```html
<script type="application/yaml" data-tracking-spec>
application: vicohome
events:
  - name: 商店页面
    type: PAGE
    tracker_type: BASE
    point: store_page
    description: 用户进入商店页面
    category: 电商
    base_schemas: ["user_info", "device_info"]
    parameters:
      - name: source
        value_type: string
        is_required: false
        description: 页面来源
</script>
```

**事件：商品详情模块曝光**

- 触发场景：商店页面中商品详情区域进入用户可视区域时触发
- 数据示例：`product_id: "SKU_12345"`
- 指标来源：商店页购买转化率（metric-spec.html）
- 指标参与：作为购买漏斗的中间环节，辅助归因分析（用户是否看到了商品信息）
- 计算公式回溯：商店页购买转化率 = 在商店页点击购买按钮的去重用户数 / 进入商店页的去重用户数 × 100%

```html
<script type="application/yaml" data-tracking-spec>
application: vicohome
events:
  - name: 商品详情模块
    type: MODULE
    tracker_type: EXP
    parent_page: store_page
    point: product_detail
    description: 商品详情区域曝光（追踪区域是否进入可视区域）
    parameters:
      - name: product_id
        value_type: string
        is_required: true
        description: 商品ID
</script>
```

**事件：购买按钮点击**

- 触发场景：用户在商店页面的商品详情模块中点击购买按钮时触发
- 数据示例：`product_id: "SKU_12345", price: 29.99, currency: "USD"`
- 指标来源：商店页购买转化率（metric-spec.html）
- 指标参与：作为"商店页购买转化率"的分子（点击购买按钮的去重用户数）
- 计算公式回溯：商店页购买转化率 = 在商店页点击购买按钮的去重用户数 / 进入商店页的去重用户数 × 100%

```html
<script type="application/yaml" data-tracking-spec>
application: vicohome
events:
  - name: 购买按钮
    type: COMPONENT
    tracker_type: CLK
    parent_page: store_page
    parent_module: product_detail
    point: buy_btn
    description: 用户点击购买按钮
    parameters:
      - name: product_id
        value_type: string
        is_required: true
        description: 商品ID
      - name: price
        value_type: float
        is_required: true
        description: 商品价格
      - name: currency
        value_type: string
        is_required: false
        description: 币种
</script>
```

**事件：支付结果上报**（SELF_DEFINE 示例 — 后端事件，不走 SPM 层级）

- 触发场景：后端收到支付回调后上报支付结果
- 数据示例：`order_id: "ORD_20260407_001", result: "success", amount: 29.99`
- 指标来源：商店页购买转化率（metric-spec.html）
- 指标参与：支付成功是购买漏斗的终点，用于验证端到端转化
- 计算公式回溯：商店页购买转化率 = 在商店页点击购买按钮的去重用户数 / 进入商店页的去重用户数 × 100%

```html
<script type="application/yaml" data-tracking-spec>
application: vicohome
events:
  - name: 支付结果
    type: SELF_DEFINE
    tracker_type: BASE
    point: payment_result
    description: 后端支付回调事件
    parameters:
      - name: order_id
        value_type: string
        is_required: true
        description: 订单号
      - name: result
        value_type: string
        is_required: true
        description: 支付结果（success/fail）
      - name: amount
        value_type: float
        is_required: true
        description: 支付金额
</script>
```

## References

- 埋点 Schema 规范详见 [references/tracking-schema.md](references/tracking-schema.md)
- SDK API 签名详见 [references/sdk-api.md](references/sdk-api.md)
- 各端实现规范详见 [references/platform-impl-spec.md](references/platform-impl-spec.md)（用户维护）
- 通用事件 Schema（自动字段、Base Schema）详见 [references/common-event-schema.md](references/common-event-schema.md)
- 埋点测试指南（L1 mock + L2 test sink）详见 [references/testing-guide.md](references/testing-guide.md)
- 埋点验证分层策略详见 [references/verification-strategy.md](references/verification-strategy.md)
- 埋点平台 API 详见 [references/api-reference.md](references/api-reference.md)
- 常见问题排查详见 [references/troubleshooting.md](references/troubleshooting.md)
