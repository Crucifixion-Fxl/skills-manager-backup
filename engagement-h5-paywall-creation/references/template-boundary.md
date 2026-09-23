# H5 模板边界与 Owner 评审

## 事实来源

每次执行以目标 Engagement checkout 的当前代码为准，先读取：

- `paywall-h5/AGENTS.md`
- `paywall-h5/src/template-kit/README.md`
- `paywall-h5/src/template-kit/public/index.ts`
- `paywall-h5/src/template-kit/public/templateKeys.ts`
- `paywall-h5/scripts/validate-template-boundaries.mjs`
- `docs/architecture/adrs/0002-isolate-paywall-templates-behind-template-kit.md`

若这些文件缺失或互相矛盾，不假定隔离已经完成，停止写代码并报告证据。

## 修改分区

| 分区 | 默认权限 | 说明 |
|---|---|---|
| `src/templates/<template>/components/**` | 允许 | 布局、视觉与交互外观 |
| `src/templates/<template>/assets/**` | 允许 | Figma 导出的模板私有素材 |
| `src/templates/<template>/model/**` | 有条件允许 | 只允许纯展示映射；不得做 IO、支付或状态编排 |
| 模板目录内样式与测试 | 允许 | 不污染全局；测试只验证模板行为 |
| `template.manifest.ts` | Owner 批准后 | 只按已批准的 key、schema、content model、capability 修改 |
| candidate 截图与差异报告 | 允许生成 | 不得自动替换 approved baseline |
| 共享 E2E spec / fixture registry | 默认禁止 | 由 Owner 提供或批准测试 harness |
| `template-kit/**` | 禁止 | 模板公共契约与内部 Controller |
| `renderer/**`、`runtime/**`、`app/**`、`services/**` | 禁止 | CMS 归一、初始化和应用编排 |
| `payment/**`、`bridge/**`、`state/**`、`tracking/**` | 禁止 | 支付、Native、状态与基础埋点 |
| `scripts/**`、package / Vite / TypeScript / CI / k8s | 禁止 | 门禁、构建和发布 |
| Marketing CMS 源代码 | 禁止 | collection、validator、transformer 和模板选项由 Owner 维护 |

模板生产代码只能依赖同模板目录、npm 包和 `src/template-kit/public`。单纯把底层函数复制进模板同样属于越界。

## 复用还是新增

比较的是结构和行为，不是像素差异：

| 维度 | 复用现有模板 | 需要 Owner 判断新模板 / 架构 |
|---|---|---|
| 内容模型 | 现有 CMS / view model 已能表达 | 需要新字段、schema 或 transformer |
| 页面骨架 | 同一主要区域，只改视觉和顺序 | 新的主要页面、弹层或多步骤流程 |
| 商品行为 | 使用已有商品选择和购买动作 | 新选择规则、支付方式或 Offer 语义 |
| Native 行为 | 使用已有 close/legal/support 等 action | 新 Native 页面、Bridge method 或成功跳转 |
| 埋点 | 使用已有 Region / Action | 新事件、字段或触发语义 |
| 共享影响 | 所有同模板 Paywall 都接受变化 | 只允许某个业务变化，其他实例必须保持原样 |

视觉变化很大但数据与交互骨架相同，仍可能复用；视觉变化不大但需要新支付或数据语义，仍属于架构变更。

## Owner 评审 issue

需要受保护变更时，在 Engagement 项目创建 issue 并交给 Paywall Owner。Issue 至少包含：

```markdown
# H5 Paywall 模板 / 框架能力评审

## 业务目标
- App / tenant：
- 目标用户：
- Paywall 场景：
- Figma 链接与 node ID：

## 与现有模板的差异
| 维度 | 现有能力 | 新需求 | 为什么不能只改 UI |
|---|---|---|---|

## 需要 Owner 决策
- 是否复用现有 templateKey：
- 是否批准新 templateKey：
- content model / schema version：
- Template Kit capability：
- Marketing CMS schema / option / validator：
- 埋点和看板是否完全复用：

## 影响范围
- 已发布的同模板 Paywall：
- iOS / Android / 支付渠道：
- 回滚方式：

## 当前状态
- 本 Skill 尚未修改任何受保护路径。
```

`templateKey` 候选可以写进 issue 供讨论，但在 Owner 明确确认前不是正式 key，不能写入 catalog、CMS 或业务代码。

## Diff 门禁

实现结束后列出相对目标分支的全部变更。发现任何未批准的受保护文件时，审查不通过；不要自动回退或覆盖用户文件，保留现场并说明来源。

必须运行：

```bash
cd paywall-h5
npm run lint:architecture
npm run lint:tracking
```

两项门禁都通过，只能证明没有已知越界和埋点绕过，不能替代 Owner review 与真机 UAT。
