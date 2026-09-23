---
name: work-method-demo-writer
description: Create product-demo-style HTML documents for explaining work methods, engineering workflows, AI coding practices, team collaboration processes, operational playbooks, and review/publishing loops. Use when the user wants to turn a methodology into a visual, animated, realistic workflow page with clear motivation, decision rules, tool demos, collaboration steps, and publishable HTML.
---

# Work Method Demo Writer

把工作方法写成**产品级、可演示、可 review 的 HTML 方法介绍页**。不要把方法写成普通文章，也不要只画抽象流程图。

详细写作、视觉和自检标准分别见：

- [产品叙事标准](references/product-story-patterns.md)
- [真实 demo 标准](references/visual-demo-patterns.md)
- [工作方法检查表](references/workflow-method-checklist.md)

## 描述

使用这个 skill 时，默认输出一个自包含 HTML 页面，放在用户指定目录；未指定时，遵循当前仓库的文档输出约定。页面要像产品 demo：读者第一屏就知道这个方法解决什么问题、适合谁、核心判断标准是什么、为什么可信。

工作方法介绍必须回答：

- 为什么需要这个方法，而不是继续沿用原来的做法？
- 哪个判断标准决定何时使用它？
- 人、AI、工具、系统之间如何协作？
- 从开始、执行、review、发布到清理，闭环是什么？
- 读者真实操作时会看到什么界面、输入什么、如何判断下一步？

## 执行流程

1. **提炼产品主张**：把标题写成清晰 offer 或结果承诺，例如“一天 1 万行代码、40 个 MR 的并行工作法”。避免“某某工作方法介绍”这种内部标题。
2. **写清 motivation**：先讲痛点和成本，再讲方法。不要一上来讲工具和步骤。
3. **确定核心判断标准**：用一句话写出方法的使用边界。例：`能用独立上下文完成的任务就拆出去`。
4. **设计端到端闭环**：把方法拆成真实生命周期：输入、判断、执行、review、发布、清理、协作反馈。
5. **制作真实 demo**：根据方法实际发生的工具界面做 mock，不用泛化卡片替代。详见 [真实 demo 标准](references/visual-demo-patterns.md)。
6. **补齐分享卡片**：如果 HTML 会发布或发给同事，添加 `description`、Open Graph 和 Twitter card meta；必要时生成本地 `og-card.svg/png`。
7. **写交互和动画**：动画必须解释状态变化；每个 step 要和对应视觉对象对齐。
8. **验证 HTML**：运行 [scripts/validate_html_doc.sh](scripts/validate_html_doc.sh) 或等价检查，至少包含 HTML parse 和 whitespace 检查。

## 规则

- Hero 用产品介绍标准：主张、适用对象、核心机制、可控收益。
- 方法论先写“为什么”，再写“怎么做”，最后写“如何 review / publish / clean up”。
- 真实 demo 是通用规则，不限于 AI coding。方法发生在哪个工具里，就复刻哪个工具的真实工作界面。
- 终端 demo 要像真实 CLI 输出；IDE demo 要像真实 IDE；review demo 要像真实评论和状态流。
- 抽象流程图只能辅助解释，不能替代真实工具 demo。
- 每个工作流 step 都要展示人的输入和系统/AI/工具的反馈。
- 不要为了显得并行而乱拆任务；写清楚“何时不该拆”。
- 输出页面默认要适配 desktop 和 mobile，避免文本溢出、控件重叠、动画和步骤不一致。
- 发布或协作 review 的 HTML 必须包含 OG/Twitter card meta；如果没有最终公网 URL，先使用相对路径，并在发布时替换为绝对 URL。

## Related Skills

按需引用相关 skill，避免重复造轮子：

- 引用前先检查当前环境是否已有对应 skill/plugin；如果缺失，不要静默跳过，要告诉用户缺少哪一个、为什么需要它，并提示用户可以安装后继续。
- 使用 `$frontend-design` 处理产品级页面布局、hero、响应式、视觉质感和 UI polish。
- 使用 `$canvas-design` 处理解释状态变化的 canvas 动画。
- 使用 `$architecture-diagram-creator` 处理系统边界、协作拓扑、平台架构图。
- 使用 `$flowchart-creator` 处理决策流、生命周期、状态机；不要用它替代真实工具 demo。
- 使用 `$algorithmic-art` 只做有意义的动态背景或抽象运动表达。
- 使用 `$doc-coauthoring` 表达多人协作、评论、版本反馈、文档共创流程。
- 使用 `$addx:gitlab-pages-html` 发布生成的 HTML 到 GitLab Pages。
- 使用 `$skill-creator` 创建或更新本 skill。
- 使用 `$skill-installer` 或可用的 plugin 安装流程，在用户确认后安装缺失的 skill/plugin。

## 输出结构

推荐页面结构：

1. Hero：产品主张 + motivation + 核心机制 + 3 个指标/收益。
2. Tool cockpit：真实工作台界面，展示方法发生在哪里。
3. In-session workflow：展示连续对话、任务维护、agent team 或同一上下文内的并行。
4. Split / handoff workflow：展示何时拆、怎么拆、如何回流。
5. Artifact isolation：展示 worktree、branch、document、ticket、dashboard 等隔离机制。
6. Review loop：展示人工 review、preview、MR、评论、验收标准。
7. Collaboration / publish：展示团队如何访问、评论、AI 如何读回反馈并修改。
8. Share card：提供 OG/Twitter card，用于 GitLab Pages、Slack/Lark、MarginDoc 或浏览器分享预览。
9. Checklist：总结使用边界、反例、清理闭环。

## 示例

### ❌ Bad

> 我们的方法是先分析需求，然后执行任务，再 review。可以使用多个 AI 并行工作，提高效率。下面是流程图。

问题：没有 motivation，没有判断标准，demo 不真实，读者不知道真实操作界面，也不知道什么时候不该使用这个方法。

### ✅ Good

> 一套面向高吞吐软件交付的 AI 协作工作台：主 session 负责收敛问题、判断边界和保护上下文质量，独立 issue/session 负责执行。标准很简单：能用独立上下文完成的任务就拆出去，避免一个 session 过杂污染 context。页面用 Ghostty/tmux、Claude/Codex、GitLens、GitLab MR、HTML Preview 和评论回读展示完整闭环。

好处：先讲为什么存在，再给判断标准，再用真实工具界面展示执行、review 和清理。

## 验证

生成或修改 HTML 后运行：

```bash
bash skills/work-method-demo-writer/scripts/validate_html_doc.sh <html-file>
```

创建或更新 skill 后运行：

```bash
uv run python scripts/validate.py --skill skills/work-method-demo-writer
```
