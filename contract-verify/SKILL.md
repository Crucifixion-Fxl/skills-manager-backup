---
name: contract-verify
description: '给"我们调但不拥有"的外部依赖建立可执行的契约验证 (contract verify) — 把对开源工具 / 内部其它服务 / 标准库 / 跨仓 API 的隐性行为假设显式化, 写成 docs/contract-verify/<tool>.md + tests/contract-verify/<tool>/ 的双向可追溯资产, 用真实依赖跑测建立 ground truth, 防止"假设漂移"再次咬代码。触发场景 (任一即可触发): (a) 用户说 "contract verify"、"契约验证"、"验证 SDK 行为"、"开源工具假设核对"、"verify external dependency"; (b) 业务代码注释里出现 "假设 X 会 Y" / "per <ADR>" / "<SDK> docs 说..." 而无法立刻验证; (c) staging/prod 出过 bug, 根因是外部依赖行为跟代码假设不符 (典型: SDK 版本升级、cross-repo HTTP 协议偏差、stdlib edge case、未读源码的中间件行为); (d) 准备升级开源依赖主版本前要回归核心假设; (e) 跨仓集成口头约定 (PE / 支付 / push / messaging) 需要落地为代码可验证的契约。本 skill 提供哲学、判据 (3 条同时成立)、章节模板、目录结构、与 L1/L2/L3 测试层的边界、维护规范、失败排查顺序。不替代 Pact 风格 consumer-driven contract test (那是 consumer 定义 provider 满足), 本 skill 是 "verify" 语义 — 我们没权力 enforce 任何东西, 只能反复验证自己的假设跟实际行为一致。'
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# contract-verify — 给"调但不拥有"的外部依赖做行为契约验证

> 本 skill 把"contract verify"作为一种独立的代码质量手段固化下来。它跟单测、集成测试、E2E 测试**互补不重叠**, 专门覆盖一类被反复咬过的根因: **我们对外部依赖的行为假设错了**。

---

## Description

给"我们调但不拥有"的外部依赖（开源 SDK / 内部其它服务 / 标准库 / 跨仓 API）建立**可执行的契约验证 (contract verify)**: 把代码里的隐性行为假设显式化, 用真实依赖跑测建立 ground truth, 防止"假设漂移"再次咬代码。

完整 7 条核心思想见 [§1 核心思想](#1-核心思想-7-条不可妥协的)。

## Rules

立 CV 需要 3 条判据**同时成立**:

1. 我们的代码做了一个**具体的行为假设**
2. 该假设的 **uncertainty > 0**（文档不全 / 没读源码 / 跨版本可能漂移 / 历史被咬过 / 跨仓口头约定 / 有 edge case）
3. **假设错了会造成真实破坏**（业务 bug / 数据错 / 链路断 / 安全问题）

完整规则:
- 触发判据 + 反向判据见 [§0 触发后第一件事](#0-触发后第一件事--判断当前对话是否真该启动-contract-verify)
- 每条 CV-XX 的 8 段固定结构见 [§3 章节模板](#3-每条-cv-xx-的章节模板-8-段固定结构)
- 落地 workflow 见 [§6 标准 workflow](#6-标准-workflow--从-0-给一个项目落地-contract-verify)
- L1 / L2 / L3 测试层的边界见 [§4](#4-与-l1--l2--l3--其它测试层的边界-必须画清)

## Examples

### ❌ Bad

跳过 §0 三条判据, 给所有外部调用都立 CV — 把 skill 稀释成无差别测试。完整反模式见 [§7](#7-反模式--看到这些立刻喊停)。

### ✅ Good

只在 §0 三条判据同时成立时立 CV。例如: SDK 版本升级前 ➜ 立"X SDK error code on timeout"的 CV-XX, 用真实 SDK 跑出 ground truth ➜ 落 docs/contract-verify/x-sdk.md + tests/contract-verify/x-sdk/。

---

## 0. 触发后第一件事 — 判断当前对话是否真该启动 contract-verify

不是所有"调了外部库"的代码都要立 CV。先按下面 3 条判据走一遍, 再决定是否往下。**滥用比不用还糟**, 会把 skill 稀释成无差别测试。

### 0.1 立 CV 的判据 (3 条必须同时成立)

```
① 我们的代码做了一个具体的行为假设 (assumption about behavior)
② 该假设有 uncertainty > 0:
   - 文档不全 / 文档与实际有偏差
   - 我们没读过实现源码
   - 跨版本可能漂移 (SDK / image / API)
   - 已经被咬过一次 (历史 bug 根因)
   - 跨仓口头约定 / 邮件约定, 没落进 schema
   - 有 edge case (null / 空字符串 / 并发 / 版本切换)
③ 假设错了会造成真实破坏 (业务 bug / 数据错 / 链路断 / 安全问题)
   — 不是纯学术问题, 不是 "理论上可能但永远不会发生" 的事
```

3 条任一不成立, 当前对话**别**启动 contract-verify, 走普通单测 / 集成测试 / 文档说明即可。

### 0.2 不能用 "第三方 vs 自己代码" 作为判据

在一个程序里, 除了"我们这次写的业务代码"以外**所有东西**都是某种"外部":
- 开源库 (`@some/sdk`, `redis-go`)
- 公司其它仓 (`services/foo`)
- 跨语言的 SDK (gRPC stub, protobuf)
- 语言标准库 (`bufio.Scanner` 默认 64KB buffer 上限)
- 运行时 (Node event loop, Go goroutine scheduler)
- 操作系统行为 (file lock, SIGPIPE)

它们都是"我们做了行为假设但不拥有实现"的对象。判据是上面 3 条, **不是**"是不是别的公司维护的代码"。

### 0.3 触发本 skill 后的标准回复格式

```
本次 contract-verify 评估 — <对象一句话>:
- 判据 ① 行为假设: <是 / 否, 一句话引代码或注释>
- 判据 ② 不确定性: <是 / 否, 哪一项 uncertainty>
- 判据 ③ 错了的破坏面: <是 / 否, 影响哪个业务路径>
→ 结论: 立 CV-XX / 暂不立 (理由)
```

不要不评估直接开干。

---

## 1. 核心思想 (7 条不可妥协的)

### 思想 1: Contract verify 是 unit test 的镜像

```
普通 unit test   =  mock 开源工具  +  测业务代码
contract verify =  mock 业务代码  +  测开源工具
```

两者**方向完全相反**:
- 单测在"外部依赖按假设行为"的前提下验证我们的代码
- contract verify 在"业务代码无关"的前提下验证外部依赖真的按假设行为

它们**不应重叠**: 单测里别去测"SDK 这个方法返什么", contract verify 里别 import 业务 handler。

### 思想 2: "verify" 不是 "define"

工业界的 "contract test" (Pact / Spring Cloud Contract) 是 **consumer-driven**: consumer 定义契约, provider 实现满足。

我们的场景**完全相反**: 外部依赖 (Novu, Kafka, GrowthBook, 第三方 API, 内部其它服务) 自己定义行为, **我们没权力 enforce 任何东西**。我们只能反复验证: "我以为它是这样工作的, 实际是这样工作的吗?"

所以叫 `contract-verify` 不叫 `contract-test`。这个命名差异是核心, **别在文档里混用**。

### 思想 3: Scope = (我们真用过的) ∩ (有不确定性的)

不给开源工具做免费回归。判据:
- ✅ **我们的代码真的依赖某个具体行为** (不是 API 存在性, 是行为细节)
- ✅ **该行为有 uncertainty** (见判据 ②)
- ✅ **错了会出业务 bug**

"覆盖所有 API" 是无底洞。一个常见 SDK 上千 API, 测全是浪费。**优先反向追溯**: 每个已发生的 bug, 问"根因是不是某个未验证的行为假设?" 是的话补 CV-XX。这比从前向"列出所有可能假设"高效得多。

### 思想 4: 测试失败时, "理解" 优先怀疑

CV 测试 fail 时, 按下面顺序排查根因, **不要颠倒**:

```
1. 外部依赖版本是否漂了?
   - image digest / SDK semver / API version 是否跟 CV 文档"实测版本"字段一致?
   - 如果漂了 → 决定: 是接受新行为更新 CV, 还是回滚版本

2. 测试本身是否写错?
   - 断言形态对吗? (await 漏了 / 异步未等齐 / spy 重置时机)
   - 数据准备对吗? (subscriber 没建 / topic 没起 / 凭据没配)

3. 我们对外部依赖的理解是否错了?  ← 真出问题这里
   → 修 docs/contract-verify/<tool>.md 的对应 CV-XX "实测行为" 段
   → 检查 "影响过的功能代码" 段列出的业务码 — 它们是否也基于错的理解写的?
   → 必要时按更新后的理解修业务码
```

**禁止**: 直接假设业务码错了去改测试或注释。这会让测试和实际依赖行为渐行渐远, 把 ground truth 反向污染。

### 思想 5: 每条 CV-XX 是一份"实测时间快照", 不是定理

外部依赖会变。每条 CV-XX 必须记:
- **实测时间** (YYYY-MM-DD)
- **实测版本** (image digest / SDK semver / API version / 协议 version)

这两个字段是判定"当前文档是否还代表真相"的唯一依据。版本漂了→ CV 进入 🔁 "待重跑" 状态, 不应被引用作为依据。

### 思想 6: 双向追溯 — 业务码 ↔ CV 章节

业务代码相关位置必须加注释:
```
// Contract: docs/contract-verify/<tool>.md#cv-XX
// per CV-XX, <行为>; <我们因此怎么写的>
```

CV-XX 章节的 "影响过的功能代码" 段反向列出业务码位置 + 状态 (已修复 / 待 audit / 反证 CV)。

**任一方向断掉, 这条 CV 都会成为孤儿** — 升级时不会重跑、bug 出来时找不到对应文档。

### 思想 7: 把隐性假设从代码注释升级为可被反复验证的资产

代码注释 (`// 假设 X 会 Y`) 和口头约定 ("PE team 说接口幂等") 是**易腐资产**:
- 新成员看不到注释
- 注释跟代码漂移没人发现
- 升级时没人去逐个 grep 注释

CV-XX 是**抗腐资产**:
- 在 docs/ 里被索引
- 有 spec 文件可被 CI 反复跑
- 失败时主动告警
- 升级触发自动重跑

**任何写在代码注释里的"假设"都是一份未升级的 CV-XX 候选**。grep 一下 "假设 / assume / 应该 / should / per <doc>", 是补 CV 的高产矿。

---

## 2. 标准目录结构

每个项目按下面结构起一遍 (本 skill 模板, 通用):

```
docs/contract-verify/
├── README.md                  ← 哲学 + 维护规范 (用本 skill §1 §3 §4 §5 §6 内容填)
├── <tool-1>.md                ← 每个工具一个 .md, CV-XX 编号在该工具内递增
├── <tool-2>.md
└── <tool-N>.md

tests/contract-verify/
├── README.md                  ← 与 L1/L2/L3 边界 + 怎么跑 + CI 调度
├── <tool-1>/
│   ├── package.json           ← 独立 deps, 不混仓 workspace (避免业务侧版本绑定影响 CV)
│   ├── docker-compose.yml     ← 只起本工具直接依赖 (e.g. Novu OSS + Mongo + Redis; 不起 Kafka/业务 DB)
│   ├── .env.example
│   ├── harness/
│   │   ├── <tool>-image.ts    ← 记录测试用 image digest + SDK ver (CV 文档同步源, single source of truth)
│   │   ├── <tool>-client.ts   ← 调外部依赖的最小 wrapper
│   │   └── stub.ts            ← mock 掉业务码 (handler / decision / template render)
│   ├── fixtures/
│   │   ├── cv-01-<slug>.fixture.ts
│   │   └── cv-02-<slug>.fixture.ts
│   └── specs/
│       ├── cv-01-<slug>.spec.ts
│       └── cv-02-<slug>.spec.ts
└── <tool-2>/  ...
```

**编号规则**: CV-XX 在每个 tool 内严格递增、**永不复用**。删掉的 CV 留位号 + 标 "[deprecated]" 一行说明何时为什么删, **不收回编号**。引用 (`per CV-04`) 才稳定。

---

## 3. 每条 CV-XX 的章节模板 (8 段固定结构)

每条 CV-XX 在 `<tool>.md` 内严格用下面 8 段, **不增不减**:

```markdown
## CV-XX <一句话标题, 描述被测的行为>

### 我们的假设
- **假设来源**: 哪条 ADR / 哪段代码注释 / 哪位同事口头说的 / 哪份文档
- **假设内容**: 用 (a)(b)(c) 列出每一条具体可证伪的子假设

### 实测行为
- **实测时间**: YYYY-MM-DD (第几轮 iter)
- **实测版本**: image digest + SDK semver + API version (从 harness/<tool>-image.ts 取)
- **raw observations**: 贴关键的实测输出 (JSON / log 片段, 截留 5-15 行核心证据)
- **真实行为**: 跟假设 (a)(b)(c) 一一对应, 用 ✅/❌/⚠️部分对 + 一句话结论

### 偏差 / 坑点 (核心结论)
- 假设 vs 实际的差距, 用表格更清晰
- 命名 / 语义 / 时序 / 错误码 / 字段透传 等具体维度
- 副发现 (实测时顺带发现的其它假设错误) 单独标 "副发现 (iter N)"

### 影响过的功能代码 (含已修复 / 待 audit)
| 文件 | 用法 | 状态 |
|---|---|---|
| `path/file.ts:line` | 简述用法 | ✅ 已修复 (commit X) / ⚠️ 待 audit / ✅ 反证 CV |

### 测试位置
- `tests/contract-verify/<tool>/specs/cv-XX-<slug>.spec.ts`
- 每个 HYP-* 子 case 列状态

### 升级 / 漂移时要重跑
- 哪些版本 bump 必须重跑此 CV (image / SDK / API protocol / 内部依赖 schema)
- 升级 release notes 出现什么关键词时主动重跑 (e.g. "durable execution" / "preference")

### 后续行动 (可选, 用完即删)
- 待 audit 的业务码列表
- 状态升级 (🟡 → ✅/❌/🔁)
- 跟上游 / 内部团队的 align 需求 (附 GitLab issue 链接)
```

**别加额外段** (changelog / history / conflicts log / decision matrix) — git history 已经覆盖, 维护成本不该额外掏。

---

## 4. 与 L1 / L2 / L3 / 其它测试层的边界 (必须画清)

每个项目都该在 `tests/contract-verify/README.md` 顶部画这张表, 数值/语言按实际填:

| 层级 | 被测对象 | 外部依赖 | 业务代码 | 跑得多快 | 触发频率 |
|------|---------|---------|---------|---------|---------|
| **L1 单测** | 业务函数 | **mock** | 进程内 import (主角) | 秒级 | 每次 commit |
| **L2 integration** | 业务模块 + 进程内集成 | **不启**, 用 fake | in-process (主角) | 秒级 | 每次 commit |
| **L3 业务 e2e** | 跨服务业务流 | **真启** | 真 host 进程 (主角) | 分钟级 | merge 前 |
| **Contract verify** ← 本 skill | **外部依赖本身** (主角) | **真启** | **mock 掉** (最小 harness, 无业务) | 分钟级 | nightly + 依赖版本 bump |

关键差异:
- **L3 验**业务通路 ("用户下单 → push 出去")
- **CV 验**外部依赖行为 ("`step.custom` 真的 cache 吗 / DELETE 真的返 404 吗")

把这两个混在一起是最常见的失败模式 — 写一个 L3 case 顺带"验 SDK 行为", 结果业务流变了 SDK case 也跟着挂, 找不到根因。

---

## 5. CI 调度 — 不阻塞 PR, 但要被定期跑

CV 测试**不应**进 PR 必跑路径 (太慢, 真依赖启动成本高)。调度策略:

1. **nightly schedule** — 每天跑一遍所有 tool 全部 CV, 结果上报到 ReportPortal / GitLab Pages, 失败发飞书 / Slack
2. **path-triggered** — 改动 `tests/contract-verify/**` / `docker-compose.yml` / SDK `package.json` 的 PR 强制跑对应 tool 的 CV (防止人无意 bump 依赖却没验)
3. **manual** — 开发本地 `make test-contract-verify TOOL=<tool> [CV=cv-XX]`, 怀疑外部依赖行为变了时手跑
4. **升级仪式** — 任何外部依赖主版本 bump 必须先跑全部相关 CV, 至少一条新行为不一致 → 阻塞升级

`make` 标准入口 (按本 skill 模板):
```bash
make test-contract-verify                  # 全部
make test-contract-verify TOOL=<tool>      # 单工具
make test-contract-verify TOOL=<tool> CV=cv-XX  # 单 CV
```

---

## 6. 标准 workflow — 从 0 给一个项目落地 contract verify

按下面 8 步走, **不要跳步**, 跳步几乎都会回头返工:

### Step 1: 列候选 tool 清单 (5-10 分钟)
- grep 业务码 import 出所有外部依赖 (SDK / cross-repo HTTP / 标准库高风险点)
- 对每个依赖按判据 ①②③ 评估, 留下 ≥ 3 项都过的
- 输出: tool 清单 + 每个 tool 预估 3-7 条 CV

### Step 2: 起 docs/contract-verify/ 框架 (10 分钟)
- 写 `docs/contract-verify/README.md` (本 skill §1 + §3 + §4 + §5 的精华)
- 每个 tool 起 `<tool>.md` skeleton (含 "部署形态 / 版本基准" 表 + 空的 "CV 总览" 表)

### Step 3: 起 tests/contract-verify/ 框架 (20 分钟)
- 写 `tests/contract-verify/README.md` (与测试层边界 + 跑法 + CI 调度)
- 第一个 tool 子目录骨架: `package.json` (独立 deps) / `docker-compose.yml` (只该 tool 必需) / `harness/` (`<tool>-image.ts` + `<tool>-client.ts` + `stub.ts`)
- 写 Makefile target

### Step 4: 反向追溯写第一条 CV (1-2 小时)
- **从已知 bug 入手, 不是从前向列假设**
- 选一个已被坑过的依赖行为 (有 commit / staging 故障 / 同事吐槽过)
- 按 §3 8 段模板写 `<tool>.md` 的 CV-01
- 写 fixture + spec, 跑通, **填 "实测行为" 段**
- 第一条 CV-01 跑通后 commit, **建立可信流程**

### Step 5: 扩 CV-02..CV-N, 跨工具
- 每条 CV 独立 fixture + spec, **不共享业务 setup** (隔离失败定位)
- 跨 tool 复制 Step 3 子目录模板

### Step 6: 业务码反向加注释
- 对每条 CV, grep 业务码里依赖该行为的位置
- 加 `// Contract: docs/contract-verify/<tool>.md#cv-XX` 注释
- 同步填 CV-XX "影响过的功能代码" 段

### Step 7: 接 CI
- GitLab CI schedule (cron)
- path-triggered (`tests/contract-verify/**`, `<sdk>/package.json` 改动)
- 失败告警接进现有渠道 (飞书 webhook / ReportPortal)

### Step 8: 维护节奏入团队 ritual
- 每月 review 一次 CV 状态 (有几条 🟡 待实测、几条 🔁 待重跑)
- 每次外部依赖升级 PR 必跑相关 CV
- 每次 staging/prod 故障 RCA 问 "是不是某条未立的 CV?" 是的话补

---

## 7. 反模式 — 看到这些立刻喊停

| 反模式 | 为什么坏 | 改成什么 |
|---|---|---|
| CV spec 里 `import` 业务 handler | 业务码改了 CV 跟着挂, 失去隔离价值 | spec 只调 SDK / 外部依赖, 业务路径全 stub |
| 一条 CV 覆盖多个 tool 的多个行为 | 失败定位指数级变难 | 一条 CV = 一个 tool 的一个具体行为假设 |
| 用 mock / fake 跑 CV | 验的是 mock 行为, 不是真依赖 | **必须**起真依赖 (docker-compose / 真 SDK / 真 HTTP) |
| CV 文档没有 "实测时间" / "实测版本" | 不知道这条 CV 是否还代表真相 | 每条 CV 头必填, 没填的 CV 视为未验证 |
| CV 失败 → 改测试断言 / 改业务码注释 让它过 | 把 ground truth 反向污染, bug 永远不会被发现 | 按 §4 思想 4 的 1→2→3 顺序排查, 99% 是改文档 + audit 业务码 |
| 升级 SDK 主版本不重跑 CV | 升级前的所有 CV 都过期了, 文档说谎 | 升级 PR 必跑相关 tool 全部 CV |
| 业务码加注释 "per CV-XX" 但 docs 里没这条 | 引用断链 | 双向追溯 (§1 思想 6) 是硬性要求 |
| 把"覆盖所有 SDK API"当目标 | 永远做不完, 价值低 | 只覆盖判据 ①②③ 都过的, 数量上少而精 |
| CV 测试塞进 L1 单测目录 | 跟单测一起跑, 拖慢 commit | CV 必须独立目录 + 独立 CI 调度 (不阻塞 PR) |

---

## 8. 高产矿 — 哪里找 CV-XX 候选

按命中率从高到低:

1. **历史 staging/prod bug 的 RCA**: 根因含 "SDK 行为跟我们以为的不一样" / "API 返了我们没预期的形态" / "中间件做了我们不知道的事" → 直接立 CV
2. **代码注释 grep**: `grep -rE "假设|assume|应该|should|per ADR|per docs|TODO: verify" src/` → 每条都是 CV 候选
3. **跨仓口头约定**: PR description / Slack 决议里 "X team 说接口是 Y 的" → 落进 CV
4. **SDK release notes "breaking changes" 段**: 升级时哪些行为变了, 哪些必须重新验
5. **`recover` / `catch` / `defer cleanup` 包住的外部调用**: 包了就是因为没把握, 没把握就该验
6. **`if err != nil && strings.Contains(err.Error(), "...")`**: 错误码 / 错误消息匹配是脆的, 验真实形态
7. **`time.Sleep` / `retry N times`**: 时序假设几乎都没文档支撑, 是 CV 高产区
8. **二进制协议 / 私有 RPC**: 没 schema, 全靠口口相传, 100% 该立 CV

---

## 9. 常见 Q&A

**Q: 跟 Pact / consumer-driven contract test 什么区别?**
A: Pact 是 consumer 写期望, provider 必须满足 (双方都改代码)。CV 是 consumer 单方验证自己的假设, **provider 不知道也不在乎** (典型: 验开源工具行为, 你不能让 Novu 改代码)。语义完全不同, 别混。

**Q: 跟 schema contract (JSON Schema / Protobuf / OpenAPI) 什么区别?**
A: Schema 验**结构** (字段名 / 类型 / 必填), CV 验**行为** (调了之后真的发生了什么)。两者互补: schema 防字段漂移, CV 防行为漂移。结构层用 schema, 行为层用 CV。

**Q: 跟 chaos engineering 什么区别?**
A: Chaos 验"依赖挂了我们怎么样", CV 验"依赖正常时它真的按我们以为的方式工作吗"。前者是 resilience, 后者是 correctness。

**Q: 一个 CV 应该多大?**
A: 一个具体的可证伪的行为假设 = 一条 CV。复杂行为拆成 (a)(b)(c) 子假设, 在同一条 CV 内的 raw observations 段里一一验, 但**不要**为子假设单独立 CV (除非子假设独立有重大业务影响)。

**Q: 同仓内服务之间的契约 (e.g. `bridge` ↔ `nc` Kafka envelope) 算不算外部依赖?**
A: 看判据。同仓 1:1 镜像 schema 走 schema lint + 单测就够 (CV 优先级低)。如果跨语言 SDK 镜像 (Go ↔ TS) 有"序列化 edge case 双方理解不一致" 风险, 那就立 CV。

**Q: CV 的 owner 是谁?**
A: 当前 squad 集体维护, 不指派个人 owner。但每条 CV 在状态升级时要在 commit message 或 CV "后续行动" 段标谁实测的, 便于 follow up 时找上下文。

**Q: 已经有 sentry / log alert 监到外部依赖问题了, 还需要 CV 吗?**
A: 需要。Sentry / log 是**事后被动**, CV 是**事前主动**。CV 在升级 PR / nightly 把 "升级把行为搞坏了" 暴露在 staging 前, 而不是等用户在生产命中。两者互补, 都要。

---

## 10. 输出形态 — 我在本次对话里到底产出什么

根据用户当前请求, 在以下里挑一个 (默认按情境推断, 不需要问):

| 用户场景 | 输出 |
|---|---|
| "我想给项目接 contract verify" (从 0 起) | 按 §6 Step 1-3 起完整骨架, 让用户挑第一条 CV |
| "这个 bug 该不该立 CV?" | 按 §0.3 格式做判据评估, 给立 / 不立结论 |
| "帮我写 CV-XX 章节" | 按 §3 8 段模板填, raw observations 段可留空待实测 |
| "升级 SDK 前要验什么" | 列该 tool 现有所有 CV, 标 "重跑必要性" 优先级 |
| "review 一下我写的 CV-XX" | 按 §3 模板 + §7 反模式表逐项 checklist |
| "把代码注释里的假设转成 CV" | grep `// 假设 / assume / per ADR`, 按判据筛, 输出 CV 候选清单 |
| "解释一下 contract verify 是什么" | 答 §1 思想 1 + §4 测试层边界表, 别铺全文 |

---

## 附: 一句话总结

> **Contract verify 把"我们对外部依赖的行为假设"从注释里的易腐资产, 升级为 docs + spec 双向可追溯的抗腐资产, 用真依赖跑出 ground truth, 防止假设漂移咬代码。它不是 unit test, 不是 e2e test, 不是 Pact — 它的方向是反的: mock 业务码, 测外部依赖。**
