# 业务全貌

> 数据来源：飞书业务规划文档（2026-05-07 林智 AI notes）+ lattice/.aide 活跃变更 + GitLab MR + 本地仓 CLAUDE.md
> 业务划分以飞书业务规划文档为准；技术细节按本地仓为准。
> Refresh：业务划分变更需读飞书原文档手动更新；其它每月 refresh。

---

## 业务大类（4 类，含基础能力）

公司业务划分为 4 个大类：3 类**业务方向**（增值 / 观测 / B 端） + 1 类**基础能力**（共享底层）。

```
┌────────────────────┬────────────────────┬────────────────────┐
│  ① 增值业务        │  ② 观测产品        │  ③ B 端业务（独立）│
│  ────────          │  ────────          │  ──────────        │
│  会员订阅          │  自然观测          │  CRM               │
│  支付              │  （Naturehood）    │  收入分享          │
│  权益中心          │  - 徽章            │  客服 / 通知       │
│  CMS 触点          │  - 社区 KBTV       │  VIP 服务          │
│  IoT 安防增值      │  - 鸟类故事        │                    │
│  IoT 平台建设      │  - Postcard 分享   │                    │
└────────────────────┴────────────────────┴────────────────────┘
                              ↑
                              │ 共享底层
                              ↓
                  ┌─────────────────────────────┐
                  │  ④ 基础能力（共享，待补完整范围）│
                  │     设备 SDK / 直播 SDK     │
                  │     Lattice 框架 / 数据 BI  │
                  │     埋点 / DevOps / 嵌入式   │
                  └─────────────────────────────┘
```

> 4 个大类的关系：① ② ③ 是**业务方向**（直接产生用户价值）；④ 是**横向能力**（支撑前 3 类）。
> 飞书原文档只明确了 ① ②，③ ④ 是结合公司实际仓库 / 团队结构推断的。

---

## 1. 增值业务

**业务定位**：负责会员订阅、支付机会、权益中心等核心能力，结合 AI 产品与服务场景，支撑 AI 能力在用户增长、付费转化等方向的工程化落地。

**当前重点**：会员订阅系统、权益系统、支付系统重构 + CMS 触点（如陪我触点）配置化。

### 1.1 会员订阅 / 支付 / 权益

**业务能力**：
- 订阅 / 续费 / 一次性购买（Stripe / Apple Pay / Google Pay / Airwallex）
- VIP 权益 / 4G VIP / 设备 VIP
- Paywall（CMS 配置）+ Promotion 弹窗
- 退款 / 客服换码

**主参与仓**：
- `lattice/cms_payment_plugin` Flutter 插件
- `lattice/flutter_payment` 旧版
- `gen3/payment-core` iOS Swift 支付核心 SDK（Airwallex/Stripe）
- `CLOUD/marketing-service` Java，AB 实验消费 + 营销业务
- `CLOUD/marketing-cms` Next.js 15 + Payload CMS — Paywall 配置后台
- `CLOUD/cms-web-preview` Flutter Web 预览
- `CLOUD/vip-service` Go VIP 权益服务

**典型业务术语**：paywall / tier / SKU / IAP / Subscription / 一次性 / 续费 / Stripe / Airwallex

**近期 30d 焦点**：
- bump-cms-payment-paywall-fix
- cms-paywall-bottom-safearea / paywall-additional-safe-area
- flutter-payment-subscription-merge
- paywall-bridge-integration

### 1.2 CMS 触点（陪我触点配置化）

**业务能力**：营销页面 / 落地页 / Promotion / Banner / Paywall 配置 + AB 实验 + 多语言运营

**主参与仓**：
- `CLOUD/marketing-cms` Next.js + Payload CMS（核心后台）
- `CLOUD/marketing-service` Java/Go 业务后端
- `IOS_MODULE/cms_payment_plugin`
- `DEV/argocd-apps` K8s GitOps

**典型业务术语**：Payload CMS / Promotion / Banner / A/B / GrowthBook / Crowdin

### 1.3 IoT 安防增值（核心营收）

**业务能力**：kb / vh（同设备 + 不同品牌壳，外部场合统一用缩写代称品牌名）— 安防摄像头主营。
- 设备绑定（Wi-Fi / 4G）+ 直播 + 录像回看
- AI 识别（人/车/包裹/宠物/鸟）+ 推送报警
- VIP 订阅（云存储 / AI 增值）
- 双向语音 / 警笛 / 探照灯

**主参与仓**：
- `SWCLIEN/g0-android` Android 原生宿主 Kotlin
- `SWCLIEN/g0-ios` iOS 原生宿主 Swift
- `SWCLIEN/g0-flutter-module` 旧 FlutterBoost 模块
- `platforms/lattice` 新 Flutter 模块化框架（替换中）
- `CLOUD/iot-service-unified` Java/Spring Boot — library/timeline/VIP/bird filter

**典型业务术语**：library / timeline / NewEvent / bird filter / live / PIR / FVP / setting-override

**近期 30d 焦点**：
- KBTV tab 三端发现 / 多语言（issue-15/16/36/50）
- Library 入口 onClick 缺失（个人中心 哑按钮）
- Timeline 接口异常（prod-us NewEvent 偶发 eventCount=0）

### 1.4 IoT 平台建设

**业务能力**：设备云平台 / 设备测试自动化 / 设备 SDK 工程化（横跨多个垂直业务的基础平台）

**主参与仓**：
- `DEVT/device-cloud-host` 设备云主控（Python）
- `DEVT/device-cloud-server` 设备云后端（Java）
- `DEVT/device-cloud-frontend` 设备云前端（TypeScript）
- `DEVT/device-cloud-camera-plugins` `device-cloud-esp32-plugin` `device-cloud-relay-plugin` 各类设备插件（Python）
- `SYS/intelli_vision_fw` 智能视觉摄像头固件（C）
- `SYSS/internal/embed_core` 嵌入式内核库
- `SYSS/vendor_sdk/*` 各芯片 vendor SDK
- `AUD/app/safertc_live/a4xlivesdk` 直播 SDK

---

## 2. 观测产品（自然观测）

**业务定位**：围绕 APP 场景的社区能力 + 分享能力，结合 AI 进行趣味玩法升级，面向客户增长 / 社区运营 / 营销运营。

**已上线功能**（飞书规划文档）：
- 徽章能力
- 社区 KBTV（刷鸟类视频）
- 鸟类故事
- Postcard 内容分享

**后续规划**：围绕营销和社区能力，持续优化迭代。

### 涉及业务能力

- 鸟类识别 + 鸟类知识图鉴（产品功能；KB 是"喂鸟器/摄像头品牌"缩写，不是 Knowledge Base 简写）
- Timeline / Story 流（用户拍到的鸟）
- PostCard 社交分享（含落地页）
- Ecosystem 探索（地理生态）
- Family Premium（家庭分享）

### 主参与仓（独立 scope）

- `applications/naturehood`（多栈）
  - `packages/` Flutter 功能包（ecosystem_api/feature + postcard_api/feature）
  - `hub/` React SPA 落地页
  - `server/` Go go-zero 后端
  - `admin/` Vue 运营后台 + Playwright E2E
- `lattice` 引用 naturehood 的 packages 集成到客户端

### 典型业务术语

ecosystem / postcard / collection / explore-tab / KB / KBTV / story / 徽章

### 近期 30d 焦点

- naturehood feature 页面国际化穿透（issue-66）
- timeline 语言穿透（naturehood-timeline-language-passthrough）
- KB 监控告警手册（issue-65 KB/release_2.15.0）

---

## 3. B 端业务（独立）

**业务定位**：CRM / 收入分享 / 客服等 B 端运营系统。**独立团队负责**，与产品 dev 主线分离。

**主参与仓**：
- `CLOUD/crm` Java + `CLOUD/crm-front` Vue 2 — CRM 客户管理
- `CLOUD/revenue-sharing` Java + `CLOUD/revenue-sharing-front` Vue — 收入分享分润系统
- `services/customer-care` TypeScript — 客服工作台
- `services/notification` — 通知服务

**新人参与可能性**：低（独立团队）。但跨部门集成需要知道 API 边界。

---

## 4. 基础能力（待补完整范围）

⚠️ **状态**：飞书业务规划文档未明确定义，本节范围由 SPM 后续补完。

**目前推断的基础能力组成**（需 SPM 确认）：

| 类别 | 仓 | 服务对象 |
|------|---|----------|
| 设备 SDK | `IOS_MODULE/smartdevicecoresdk-ios` | IoT 安防增值 + IoT 平台 |
| 直播 SDK | `AUD/app/safertc_live/a4xlivesdk` | IoT 安防增值 |
| Lattice 框架 | `platforms/lattice` | 全部客户端（增值 + 观测） |
| 数据 BI / 埋点 | `data/tracker-management` `data/bi` `data/lowcode-engine` | 全业务 |
| AB 实验 | GrowthBook（外部 SaaS） + `data/growthbook-cli` | 增值业务为主 |
| 多语言 | Crowdin + lattice 内 `tools/crowdin_sync` | 全业务 |
| 统一日志 | `CLOUD/a4x-logger-sdk` | 全后端 |
| K8s / GitOps | `DEV/argocd-apps` `DEV/IaC` `DEV/k8s` | 全后端 |
| 微前端 | `frontend/web` `frontend/micro_app_platform` | B 端 + 增值后台 |
| QA / 测试 | `qa-workspace/qa-tools` | 全业务 |
| 工程基础 | `engineering/skills` | 全员开发 |

**待 SPM 决策**：
- [ ] 基础能力是否拆成独立大类（与增值/观测平级）？
- [ ] 还是归入"增值业务"内的"IoT 平台建设"？
- [ ] 还是部分归"基础"，部分归对应业务？
- [ ] 哪些团队 / 角色专属基础能力？

---

## 新人参与哪条主线？（全栈 × 主线归属感）

公司开发规则：**产品 dev 全栈，归属 1-2 条业务主线深耕，主线内跨栈**。

不再按"前端 / 后端 / 客户端"分人，而是按"业务大类 → 主线"找人。

| 业务大类 | 推荐归属主线 | 涉及栈（全栈期望全部能动手） |
|---------|------------|---------------------------|
| 增值 1.1 订阅/支付/权益 | 支付主线 | iOS Swift / Flutter / Java / Next.js |
| 增值 1.2 CMS 触点 | CMS 主线 | Next.js + Payload / Java / Flutter Web |
| 增值 1.3 IoT 安防（核心营收） | 客户端主线 | Android Kotlin / iOS Swift / Flutter / Java + 数据埋点 |
| 增值 1.4 IoT 平台建设 | 平台主线 | Python / Java / TypeScript / C 嵌入式 |
| 观测 2 自然观测 | Naturehood 主线 | Flutter / Go / React / Vue + 数据埋点 |
| B 端 3 | 独立团队 | Java / Vue 2 → React 迁移中 |
| 基础能力 4 | 独立 / 跨主线 | 视具体能力定 |

> "全栈"：每条主线内的所有栈你都应能动手做基础修改 + 提 MR。
> "归属感"：每个 dev 主要在 1-2 条主线深耕（半年-1 年），熟悉该主线业务上下文 + 历史决策 + 关键代码。

### 独立团队（不强制全栈业务开发，但有跨技能要求）

| 团队 | 跨技能要求 | 路由 skill |
|------|---------|-----------|
| **QA** | 测试左移：QA 兜底 E2E + 探索性，开发负责 unit/integration | `e2e-testing` `qatools` `ai-regression-testing` |
| **SRE / DevOps** | 全员必须有 CI/CD 能力 | `gitlab-ci` `argocd` `argocd-deploy` `cicd-developer` `k8s-ops` |
| **数据 / BI** | 全员必须有数据驱动思维 + 软硬技能 | `data-driven-investigation` `tracking-lifecycle` `growthbook` `superset` `dagster` |
| **算法** | 同上 + AI 工程能力 | `claude-api` `agentic-engineering` |

> 所有产品 dev（不仅独立团队）也都需要掌握 CI/CD + 数据驱动思维这两套基础能力。

---

## 飞书业务规划原文摘要（2026-05-07 林智）

> Title: 增值业务与观测产品功能规划
>
> **增值业务**
> - 负责会员订阅、支付机会、权益中心等核心能力，结合 AI 产品与服务场景，支撑 AI 能力在用户增长、付费转化等方向的工程化落地。
> - 例如进行会员订阅系统、权益系统和支付系统的重构，以及 CMS 触点如陪我触点的配置化。
>
> **观测产品**
> - APP 场景：围绕两类 APP 的社区能力和分享能力场景，面向客户增长、社区运营和营销运营等场景。
> - 结合 AI 能力进行趣味玩法升级，已上线徽章能力、社区 KBTV 能力（刷鸟类视频）、鸟类故事以及 Postcard 内容分享等功能。
> - 后续规划：围绕营销和社区能力，持续优化迭代相关功能开发。

> 飞书原链接：https://mg5nag3zsf.feishu.cn/docx/Bm9YdpiL0oWZrxx4qTeclH0onIb
