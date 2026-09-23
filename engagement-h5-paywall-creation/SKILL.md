---
name: engagement-h5-paywall-creation
description: Create or update Engagement H5 Paywall template UI from a Figma prototype, prepare the matching Marketing CMS draft, and verify visual, interaction, payment-contract, and framework-owned tracking behavior. Use when product or operations asks to build, redesign, or configure an H5 Paywall. Do not use for only connecting an existing Paywall to a touchpoint.
---

# Engagement H5 Paywall Creation

## Description

把产品运营提供的 Figma 原型和业务配置转换为可验收的 H5 Paywall。产品运营只需要描述业务，不需要理解支付、Bridge、运行时和埋点实现。

本 Skill 只修改模板业务 UI。支付、价格、商品选择状态、Native Bridge、成功/失败处理和基础埋点由 Paywall 框架复用，不在模板中重新实现。

## 路由

- 只在 Marketing CMS 中配置现有标准组件、文案或商品，不改 H5 模板代码：使用 `marketing-cms`。
- 只需要设计、生成和上传 Paywall 头图：使用 `paywall-hero`。
- Paywall 已存在，只需从 App 触点打开并验证归因：使用 `engagement-touchpoint-integration`。
- 根据 Figma 新增或调整 H5 模板 UI：使用本 Skill。
- 需求涉及新 `templateKey`、新框架能力或受保护代码：先走 Owner 评审，本 Skill 不实施该部分。

## Rules

1. 产品运营只提供 Figma 和业务配置，不承担底层技术设计。
2. 默认写入范围只有模板业务 UI；所有框架、交易、跨端、埋点和发布代码均受保护。
3. `templateKey` 只能复用已注册值；新增值必须由 Paywall Owner 评审和注册。
4. 一旦需求超出 Template Kit 已有能力，先创建 Owner issue，并停止相关代码修改。
5. CMS 先写 Staging Draft；发布和跨环境传输必须分阶段验证并获得明确授权。
6. 所有完成结论必须对应可复查证据，不用 Mock 结果替代真机、真实支付、权益或数仓验证。

## 产品运营提供什么

优先从 Figma 和现有配置推断，不要求用户先理解技术字段。最少需要：

1. Figma 文件或原型链接，包含要实现的 frame；多分群、多设备档位或弹层需要给出对应 frame。
2. 业务场景、目标 App / tenant、目标用户和预计入口。
3. Paywall 名称或业务 key；没有时可以提出候选，但发布前必须由业务确认。
4. 商品 ID、Offer 类型 / Offer ID、默认商品；个性化页面还需要目标分群。
5. 无法从 Figma 判断的行为差异，例如关闭、All Plans、恢复购买、成功后跳转或特殊支付方式。
6. 英文源文案及需要覆盖的语言；Figma 文案不是最终文案时要明确来源。

不要要求产品运营提供事件名、埋点字段、Bridge 方法、支付 payload、CMS 原始 JSON 或自动生成的 `templateKey`。这些不是业务输入。

## 启用门槛

写代码前必须在目标 Engagement checkout 中确认：

- 已读取 `paywall-h5/AGENTS.md`；
- `paywall-h5/src/template-kit/README.md`、`src/template-kit/public/index.ts`、`src/template-kit/public/templateKeys.ts` 和 `src/renderer/templateRegistry.ts` 存在；
- `paywall-h5/scripts/validate-template-boundaries.mjs`、`validate-tracking-guardrails.mjs` 及对应测试存在；
- `paywall-h5/package.json` 提供 `lint:architecture` 和 `lint:tracking`，且两条命令均可执行通过；
- 工作区没有会与本次模板修改重叠的用户改动。存在重叠时改用独立 worktree，不覆盖现有修改。

任一门槛不满足，只能完成 Figma 分析、模板复用判断和 Owner 评审材料，不得修改 H5 代码。不得写死任何个人工作区绝对路径；用 Git 仓库根目录和仓库相对路径定位文件。

详细边界见 [references/template-boundary.md](references/template-boundary.md)。

## 工作流

### 1. 冻结输入与验收面

读取 Figma 的目标 frame、组件属性、布局尺寸、字体、颜色、圆角、间距、素材和交互状态。记录 file key、node ID 和读取时间；无法读取 Figma 时，请用户提供导出的 frame 和原始素材，不根据模糊截图猜完整页面。

形成简短输入表：

```yaml
figma:
  source: <url>
  frames: <node IDs and scenario mapping>
business:
  tenant: <app or tenant>
  paywall_key: <confirmed or proposed>
  audience: <standard or segment list>
commerce:
  products: <product IDs>
  offers: <offer type and offer ID>
  default_product: <product ID>
behavior:
  capabilities: <only behavior visible in the design or explicitly requested>
locales:
  source: en
  targets: <required locales>
```

敏感账号、支付凭证和生产 token 不进入该文件或 MR。

### 2. 判断复用模板还是申请新模板

读取当前代码，不假定模板永远只有两个：

- `paywall-h5/src/template-kit/public/templateKeys.ts`
- `paywall-h5/src/templates/*/template.manifest.ts`
- `paywall-h5/src/templates/*/`
- Marketing CMS 当前 Paywalls collection 的 `templateKey` 选项和 validator

按内容模型、页面骨架和框架 capability 比较 Figma 与现有模板：

- 只是颜色、排版、素材、文案、卡片样式、区块顺序或模板已有状态的视觉变化，优先复用现有模板。
- 需要新的内容模型、交互流程、支付/导航语义、框架 capability 或会破坏同模板其他已发布 Paywall，按新模板或架构变更处理。
- 修改共享模板前，盘点该模板在 Staging、Pre 和可获得的 Production 发布清单中的使用范围；无法确认线上影响时，不直接修改。

`templateKey` 不自动生成。需要新增时，先创建 Owner 评审 issue；只有 Owner 已确认稳定 key、内容模型、capability 和 CMS 方案后，才继续模板 UI 工作。

### 3. 建立安全的实现范围

默认只允许修改：

- `paywall-h5/src/templates/<approved-template>/components/**`
- `paywall-h5/src/templates/<approved-template>/assets/**`
- `paywall-h5/src/templates/<approved-template>/model/**` 中纯展示映射
- 模板目录内的局部样式、单元测试和可访问性 / UI 自动化标识

`template.manifest.ts` 只有在 Owner 已给出准确的 key、content model、schema version 和 capability 后才可按批准内容修改。Figma 基线、共享 E2E harness 和 CMS schema 不属于产品运营的默认写入范围。

开始实现前列出 planned changed files。若出现受保护路径，立即停止并进入 Owner 评审，不能用“为了让测试通过”为理由越界。

### 4. 实现 Figma UI

- 模板只导入同一模板目录、npm 包和 `src/template-kit/public`。
- 用 Template Kit 的 Controller、Region 和 Action 组合商品选择、购买、关闭、No Thanks、All Plans、恢复购买、法律页面和重试。
- 不调用 `fetch`、Bridge、TrackingService、全局 state、支付状态机或 Native API。
- CSS 和素材放在模板目录，避免修改全局样式影响其他模板。
- 价格、折扣、Offer、商品选择和按钮状态必须来自只读 view model / controller，不能把商店价格写死在 UI。
- 所有交互控件提供可读名称和稳定测试标识；处理长文案、小屏、安全区、加载、空数据和错误状态。
- 基础曝光、CTA、支付成功和支付失败埋点由框架语义组件托管。模板不得自行补报或改名。

如果 Figma 需要 Public Template Kit 没有的行为，停止实现该行为并提交 Owner 评审，不得在模板中绕过。

### 5. 准备 Marketing CMS 草稿

先在 Staging 操作，使用 `marketing-cms` 完成鉴权和安全写入：

- 复用标准模板时，`templateKey` 留空，内容由标准 components 配置。
- 复用个性化模板时，选择当前已注册的个性化 `templateKey`，按当前 validator 配置完整商品矩阵。
- 新模板必须等 Engagement catalog 和 Marketing CMS 选项都由 Owner 合入后才能选择。
- 先 Save Draft 和预览；英文内容确认后再触发翻译。
- 未经用户明确要求，不执行 Staging → Pre 或 Pre → Prod transfer。

CMS 详细规则和数据请求关系见 [references/cms-configuration.md](references/cms-configuration.md)。

### 6. 自动验证

在 `paywall-h5` 中至少运行：

```bash
npm run lint
npm run lint:architecture
npm run lint:tracking
npm test
npm run build
npm run test:e2e:contract
```

再运行与目标模板对应的交互和视觉 suite。必须覆盖：

- Figma 关键 frame 与页面截图对比；
- iOS / Android WebView viewport、长文案和安全区；
- 价格 loading / success / partial / error；
- 商品默认选中、切换、Offer 与购买参数；
- CTA、关闭、法律页面、重试及模板声明的其他 capability；
- Mock Bridge 的 PV、区域曝光、CTA、支付取消 / 成功 / 失败调用序列。

Mock 结果只证明浏览器 UI 和契约，不证明真实价格、真实支付、权益到账或数仓入库。不得自动覆盖已批准的视觉 baseline；先输出 candidate 和差异报告，等人工确认。

完整验收矩阵见 [references/verification.md](references/verification.md)。

### 7. 提交与交付

提交前检查最终 diff 只包含批准的业务 UI 范围和证据文件。MR 描述必须包含：

- Figma 来源与 frame 映射；
- 复用 / 新增模板结论及理由；
- 实际 changed files；
- CMS Draft key、目标环境与商品配置摘要；
- 自动化命令和结果；
- candidate 截图或视觉差异；
- 未完成的真机、真实支付、权益和看板验证；
- Owner issue / approval（如有）。

MR 只能声明“模板实现完成”或“浏览器契约通过”，不能在真机门禁前声明可发布。最终合入和任何底层修改由 Paywall Owner 决定。

## Owner 评审触发条件

出现任一情况，先创建 issue 给 Paywall Owner，停止相关代码修改：

- 新增或修改 `templateKey`、template catalog、content model 或 schema version；
- 修改 Template Kit public/internal、renderer、runtime、payment、bridge、state、tracking、services、App 分发；
- 新增支付、导航、关闭、恢复购买、成功后跳转或 Native capability；
- 新增 / 修改基础埋点、业务自定义事件或通用看板口径；
- 修改 Marketing CMS collection、template 选项、validator、transformer 或跨环境同步；
- 修改构建脚本、架构门禁、package 配置、CI、部署；
- 共享模板影响范围无法确认，或会改变未在需求内的已发布 Paywall。

Issue 内容模板见 [references/template-boundary.md](references/template-boundary.md#owner-评审-issue)。

## Examples

### Bad Example

用户给出一个带新支付步骤的 Figma 后，直接在模板中调用 Bridge、手写支付状态和埋点，再自动注册一个新的 `templateKey`。这同时越过 Template Kit、Owner 审批和 CMS catalog 边界，必须阻止。

### Good Example

用户要求按照 Figma 调整个性化 Paywall 的 Hero、商品卡和 CTA 外观。先确认现有个性化模板的数据模型与交互能力足够，并盘点共享影响；随后只修改该模板目录中的组件、素材和局部样式，使用现有 Template Kit Action，准备 Staging CMS Draft，运行 lint、单测、构建、Bridge contract 和视觉 candidate，最后把真机与支付验证标为待完成。

## 最终输出

向产品运营返回简洁的结果表：

| 项目 | 结果 |
|---|---|
| 模板策略 | 复用 `<key>` / 等待 Owner 批准新模板 |
| H5 修改 | 仅列业务 UI 文件 |
| CMS | Draft key、环境、模板、商品与 Offer 摘要 |
| Figma | 已覆盖 frame 与待确认差异 |
| 自动化 | lint / unit / build / contract / visual 结果 |
| 框架能力 | 价格、支付、Bridge、成功失败和基础埋点均复用；无模板侧实现 |
| 发布门禁 | Staging、真机、支付、权益、看板的完成状态 |
| Owner 事项 | 无 / issue 链接和待批准内容 |

## 禁止事项

- 不自动生成 `templateKey`。
- 不修改受保护路径，即使用户只说“顺便修一下”。
- 不在模板中重新实现价格、支付、Bridge、状态机、成功/失败处理或基础埋点。
- 不把 Mock 支付成功当作真机支付成功。
- 不因视觉测试失败直接更新 baseline。
- 不把 Draft 当作 App 可见内容，不跳过 Pre 直接发布 Prod。
- 不覆盖工作区中已有的用户修改，不硬编码个人目录。
