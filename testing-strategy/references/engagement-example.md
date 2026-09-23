# Engagement 项目测试方案 — 实战范例

> 本文档是 `testing-strategy` skill 的参考实现，展示后端+APP（Java Spring Boot + Flutter Web）项目如何落地分层测试策略。
> **读此文件的时机**：生成测试方案时参考文档结构、表格格式、命名规范和场景组织方式。

---

## 1. 文档目录结构

```
docs/testing/
├── strategy.html                 # 测试方案总览（SSOT，≤600 行）
└── scenarios/                    # 场景矩阵（按 Epic + 技术模块拆分）
    ├── ep1-feature-gate.html     # Epic 1: 功能门控（产品视角）
    ├── ep2-proactive.html        # Epic 2: 主动引导
    ├── ep3-subscription.html     # Epic 3: 订阅动线
    ├── tech-evaluation-service.html # 技术: 评估服务
    ├── tech-engagement-service.html # 技术: 互动记录
    ├── tech-api-contract.html    # 技术: API 契约
    ├── tech-cms-content.html     # 技术: CMS 内容完整性
    └── tech-nfr.html             # 技术: NFR 降级容错
```

**组织原则**：
- `strategy.html` 只放总览（层级表、分层逻辑、Mock 架构、追溯矩阵、CI/CD），不放具体用例
- 具体用例拆到 `scenarios/`，按两个维度组织：
  - **Epic 文件**（`ep*.html`）：产品/QA 看，按 User Story → AC 场景组织
  - **技术文件**（`tech-*.html`）：开发者看，按服务模块组织
- 两种文件共享**同一套用例编号**，互相引用
- 所有页面共用 `testing-strategy` 的 HTML shell：页内大纲、summary cards、`.table-wrap`、质量门禁、相关链接。本文为阅读效率用简化表格表达内容契约；生成项目文档时必须输出 HTML `<table>`。

---

## 2. strategy.html 结构（总览文档）

### 2.1 测试层级总览（10 列标准表格）

| 层级 | 用例数 | 测试目标 | 真实依赖 | mock 依赖 | 真实 infra | mock infra | 执行时机 | 耗时 | 代码位置 |
|------|-------|---------|---------|----------|-----------|-----------|---------|------|---------|
| **L1-backend** | 26 | 核心业务逻辑 | — | DB/HTTP（Mockito）| — | — | 每次提交 | < 30s | `src/test/` |
| **L1-app** | 107 | 组件/状态逻辑 | — | ApiClient（mockito）| — | — | 每次提交 | < 30s | `app/test/` |
| **L2-1-backend** | 7 | API 响应结构不回退 | — | 所有 Service（MockMvc）| — | — | 每次提交 | < 1min | `src/test/.../api/` |
| **L2-2-backend** | 19 | 服务协作、DB 读写 | — | GrowthBook/Entitlement/CMS（WireMock）| MySQL（Docker）| — | PR + Nightly | < 10min | `src/test/.../integration/` |
| **L3-1-backend** | 62 | 完整集成（含真实 CMS + GB）| Payload CMS, GrowthBook | Entitlement（WireMock）| MySQL + MongoDB + GB（Docker）| — | Release 前 | < 15min | `src/test/.../e2e/` |
| **L3-2-app（自动）** | 55 | Flutter 渲染 + US AC | CMS, GrowthBook | Entitlement（WireMock）| MySQL + MongoDB + GB（Docker）| WireMock | 每次 MR | < 30min | `tests/l3/` |
| **L3-3-backend** | ~20 | staging 配置完整性 | staging CMS, staging GB | Entitlement（WireMock）| MySQL（Testcontainers）| — | Deploy 后 | < 5min | `src/test/.../staging/` |
| **L4** | — | 业务验收、视觉还原 | 全部（Staging）| — | 全部 | — | 发布前 | 手工 | — |

**表格设计要点**：
- **真实依赖 vs mock 依赖** 列是分层的核心决策依据——明确每层哪些是真实的，哪些是 mock 的
- **代码位置** 列帮助开发者快速定位测试代码
- **用例数** 列体现测试金字塔比例

### 2.2 分层逻辑表（每层解决什么上层解决不了的问题）

| 层级 | 解决的核心问题 | 为什么上层不够 |
|------|-------------|--------------|
| **L1** | 业务逻辑正确性（状态机、冷却计算） | 单测足够，不需要进程外依赖 |
| **L2-1** | API 结构不回退 | L1 不启动 HTTP 路由，契约破坏只有走 HTTP 才能发现 |
| **L2-2** | SQL/ORM/事务/DB 写入正确性 | L1 mock 了 DB，无法验证真实 SQL |
| **L3-1** | CMS schema 变更、GrowthBook flag 格式变更 | L2-2 mock 了外部依赖，上游格式变化对 L2-2 透明 |
| **L3-2** | Flutter 渲染、Widget 与 API 集成、US AC 端到端 | L3-1 无 App，用户行为路径只有真实浏览器才能还原 |
| **L3-3** | staging 环境配置正确（CMS slotName 存在、blockType 正确） | L3-1/L3-2 用 Docker 本地 CMS，不验证运营配的真实内容 |
| **L4** | 视觉还原、真机交互、真实订阅支付 | E2E 跑 Flutter Web，无法验证原生真机体验 |

### 2.3 左移原则与反模式

| 验证点 | 首次出现的层级 | 说明 |
|--------|-------------|------|
| 冷却计算、状态机、filter 规则 | **L1** | 纯逻辑，单测最快 |
| SQL 正确性、DB 写入、事务边界 | **L2-2** | 最早接触真实 MySQL 的层 |
| CMS 字段完整性、GrowthBook flag 格式 | **L3-1** | 比等到 L3-2 早一个层级暴露 |
| US AC 验证（用户视角） | **L3-2** | 端到端，不可省略 |

**禁止的反模式**：
- 在 L3-2 才第一次验证冷却逻辑（应在 L1 已覆盖）
- 因为"L1/L2 已经测了"而省略 L3-2 中对应的 US AC 用例
- 把 CMS schema 验证推到 L3-2 smoke 才跑（应在 L3-1 先捕获）

### 2.4 Mock 基础设施 SSOT

所有 mock 配置统一在 `marketing-service/mock/`，多层共用同一套，不重复维护：

```
marketing-service/mock/
├── docker-compose.yml               # MySQL + WireMock（L2-2/本地开发用）
├── fixtures/
│   ├── base/01-schema.sql           # 建表（启动自动加载）
│   └── scenarios/
│       ├── new-user/data.sql        # 无 Engagement 记录
│       ├── returning-user/data.sql  # 有冷却期记录
│       └── converted-user/data.sql  # 所有触点已转化
└── stubs/
    ├── growthbook/evaluate-flags.json
    ├── entitlement-service/
    │   ├── no-subscription.json     # 空权益
    │   └── with-subscription.json   # 含订阅
    └── payload-cms/                 # 仅 L2-2 用；L3-1/L3-2 连真实 CMS
```

**WireMock 按层级切换策略**：

| 外部系统 | L2-2 | L3-1 | L3-2 |
|---------|------|------|------|
| GrowthBook | WireMock stub | **真实** Docker | **真实** Docker |
| Payload CMS | WireMock stub | **真实** Docker + autoInit seed | **真实** Docker + autoInit seed |
| Entitlement | WireMock stub | WireMock / @MockBean | WireMock stub |

---

## 3. 场景文件结构（scenarios/*.html）

### Epic 文件格式（ep*.html）——产品视角

按 User Story 分节，每个 US 一张 AC 级追溯表：

```html
<section id="us-tp-01">
  <h2>US-TP-01 设备卡片付费角标</h2>
  <div class="table-wrap">
    <table>
      <caption>AC 级追溯矩阵</caption>
      <thead><tr><th>AC 场景</th><th>Smoke</th><th>L1</th><th>L2-1</th><th>L2-2</th><th>L3-1</th><th>L3-2</th><th>L4</th></tr></thead>
      <tbody>
        <tr><td>未订阅显示锁定角标</td><td>yes</td><td>EvaluationSvcTest</td><td>CONTRACT-001,002</td><td>EVAL-001</td><td>FG01-001</td><td>TP01-001</td><td>yes</td></tr>
        <tr><td>点击角标进 Paywall</td><td>no</td><td>FeatureGateWidgetTest</td><td>-</td><td>EVAL-001</td><td>FG01-002</td><td>TP01-002</td><td>-</td></tr>
        <tr><td>已订阅显示激活态</td><td>no</td><td>FeatureGateWidgetTest</td><td>-</td><td>EVAL-002</td><td>FG01-003,004</td><td>TP01-004</td><td>yes</td></tr>
      </tbody>
    </table>
  </div>
</section>
```

### 技术文件格式（tech-*.html）——开发视角

按技术关注点分节：

```html
<section id="touchpoint-evaluation-service">
  <h2>测试场景矩阵 — 技术模块: TouchpointEvaluationService</h2>
  <div class="table-wrap">
    <table>
      <caption>技术模块追溯矩阵</caption>
      <thead><tr><th>测试场景</th><th>Smoke</th><th>L1</th><th>L2-1</th><th>L2-2</th><th>L3-1</th><th>L3-2</th><th>L4</th></tr></thead>
      <tbody>
        <tr><td>GrowthBook 启用 + 空权益 → locked=true</td><td>yes</td><td>EvaluationSvcTest</td><td>CONTRACT-002</td><td>EVAL-001</td><td>FG01-001</td><td>-</td><td>-</td></tr>
        <tr><td>Entitlement 含订阅 → locked=false</td><td>no</td><td>EvaluationSvcTest</td><td>-</td><td>EVAL-002</td><td>FG01-004</td><td>-</td><td>-</td></tr>
        <tr><td>GrowthBook 超时 → 降级空列表</td><td>no</td><td>-</td><td>-</td><td>EVAL-007</td><td>NFR-003</td><td>-</td><td>-</td></tr>
      </tbody>
    </table>
  </div>
</section>
```

**AC 级追溯表格设计要点**：
- 列固定为 `场景 | Smoke | L1 | L2-1 | L2-2 | L3-1 | L3-2 | L4`
- 每个格子填**具体用例编号**，无覆盖填 `-`
- `Smoke` 列用 `yes/no` 或 badge 表示该场景是否在 CI smoke 中覆盖；不要只靠颜色表达
- **同一个用例编号出现在 Epic 文件和技术文件中**，保证双向追溯

### 用例编号规范

| 维度 | 格式 | 示例 |
|------|------|------|
| L2-1 契约 | `L2-1-CONTRACT-NNN` | L2-1-CONTRACT-001 |
| L2-2 集成 | `L2-2-<MODULE>-NNN` | L2-2-EVAL-001, L2-2-ENG-003 |
| L3-1 后端 E2E | `L3-1-<EPIC>NN-NNN` | L3-1-FG01-001（Feature Gate, US-TP-01）|
| L3-1 技术 | `L3-1-<MODULE>-NNN` | L3-1-NFR-001, L3-1-CMS-001 |
| L3-2 App E2E | `L3-2-TPNN-NNN` | L3-2-TP01-001（US-TP-01）|
| L3-2 技术 | `L3-2-NFR-NNN` | L3-2-NFR-001 |
| L3-3 Staging | `L3-3-<MODULE>-NNN` | L3-3-CMS-001 |

---

## 4. 测试代码目录结构

```
# 后端测试（与 src 同级）
marketing-service/src/test/java/.../
├── unit/                    # L1 单测
│   └── touchpoint/domain/   # 按领域模块组织
├── api/                     # L2-1 API 契约测试
├── integration/             # L2-2 集成测试
│   ├── evaluation/          # 按业务能力组织
│   └── engagement/
├── e2e/                     # L3-1 后端完整集成
└── staging/                 # L3-3 staging 配置验证

# E2E 测试（独立目录）
tests/
├── l3/                      # L3-2 App E2E
│   ├── feature-gate.spec.ts # 按触点类型组织
│   ├── proactive.spec.ts
│   └── cache.spec.ts
├── l3-staging/              # L3-3 App staging 验证
├── helpers/flutter.ts       # Flutter Web 辅助函数
└── playwright.config.ts
```

---

## 5. Flutter Web + Playwright E2E 详细配置

### Playwright 配置

```typescript
// playwright.config.ts
export default defineConfig({
  testDir: './tests/l3',
  timeout: 60_000,            // Flutter Web 冷启动慢
  expect: { timeout: 15_000 },
  workers: 1,                 // Flutter Web 不支持并行
  projects: [{ name: 'chromium', use: { headless: true } }],
  webServer: {
    command: 'flutter run -d web-server --web-port 5173 --web-renderer html',
    port: 5173,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
```

### Flutter Semantics 标注规范

Flutter widgets 需添加 `Semantics(label: '...')` 才能被 Playwright 通过 ARIA 定位。TDD 顺序：先写 Playwright 测试（RED），再在 widget 添加 Semantics 标注（GREEN）。

| Widget | Semantics label | 渲染条件 |
|--------|----------------|---------|
| FeatureGate 锁定 overlay | `touchpoint-locked-<slotName>` | locked=true |
| Modal Dialog | `touchpoint-modal-<slotName>` | PROACTIVE Modal |
| Banner Container | `touchpoint-banner-<slotName>` | PROACTIVE Banner |
| 关闭按钮 | `dismiss-<slotName>` | 所有 PROACTIVE |
| 升级 CTA | `cta-<slotName>` | actionType=OPEN_PAYWALL |

### 辅助函数

```typescript
// helpers/flutter.ts
export async function waitForFlutterReady(page: Page) {
  await page.waitForSelector('flutter-view, flt-glass-pane', { timeout: 30_000 });
}
export async function enableSemantics(page: Page) {
  const placeholder = page.locator('flt-semantics-placeholder');
  if (await placeholder.isVisible()) await placeholder.click();
}
```

---

## 6. WireMock 容错注入示例

```json
// 注入 5s 延迟 → 预期：降级返回 HTTP 200 空列表
{ "request": { "method": "POST", "urlPattern": "/api/eval" },
  "response": { "fixedDelayMilliseconds": 5000, "status": 200 } }

// 返回 500 → 预期：降级 locked=true
{ "request": { "method": "GET", "urlPattern": "/v1/features/.*" },
  "response": { "status": 500 } }
```

---

## 7. 质量门禁

| 门禁 | 检查点 | 标准 |
|------|-------|------|
| CI Gate | L1 + L2-1（每次提交） | 100% 通过，覆盖率 ≥ 80% |
| PR Merge | L1 + L2-1 + L2-2 + **L3-2 smoke** | 100% 通过 |
| Release | L1 + L2 + **L3-1 + L3-2 全量** | 100% 通过 |
| Deploy-to-staging | L3-3 staging 验证 | 100% 通过 |
| 发布前 | L4 验收 | PM / QA 确认 |
