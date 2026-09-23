# Product Story Patterns

Use this reference when rewriting the hero, section leads, CTA copy, and method narrative.

## Hero

Hero copy must read like a product introduction, not an internal memo.

Use this shape:

1. **Result / offer**: what this method helps achieve.
2. **Audience / setting**: who uses it and in what work environment.
3. **Mechanism**: the core operating model.
4. **Control**: how quality, review, cleanup, or safety stays manageable.

Example:

> 一套面向高吞吐软件交付的 AI 协作工作台：主 session 负责收敛问题、判断边界和保护上下文质量，独立 issue/session 负责执行。标准很简单：能用独立上下文完成的任务就拆出去。

## Motivation

Write the motivation before the mechanics.

Good motivation names the hidden cost:

- Waiting cost: people wait while tools generate, test, query, render, or deploy.
- Context cost: one long session gets polluted by unrelated tasks.
- Review cost: outputs become hard to inspect when changes are mixed.
- Coordination cost: teammates cannot comment on or reproduce the workflow.

Avoid vague motivation:

- “提高效率”
- “流程更清晰”
- “方便管理”

Replace it with a concrete mechanism:

- “把等待时间转成并行产出”
- “用独立上下文保护 AI 输出质量”
- “一个 issue/MR 对应一个可 review 的工作单元”

## Decision Rule

Every method needs a short decision rule.

Format:

```text
When <condition>, do <method>; otherwise <fallback>.
```

Examples:

- 能用独立上下文完成的任务就拆出去；否则留在主 session 收敛。
- 需要团队评论时发布 HTML；只需要个人检查时用本地 Preview。
- demo 必须复刻真实工作界面；抽象流程图只能做辅助。

## Section Leads

Each section lead should answer one reader question:

- Why this step exists
- What changes in the workflow
- What the reader should inspect in the demo
- What mistake this step prevents

Avoid “本章介绍……” phrasing. Write the point directly.

## CTAs

Use CTAs that match workflow stages:

- 看并行闭环
- 从工具工作台开始
- 查看 review 链路
- 打开发布与协作流程

Avoid generic CTAs:

- 了解更多
- 查看详情
- 点击这里

## OG / Social Card

When the HTML is meant to be published or reviewed by teammates, add share metadata:

```html
<meta name="description" content="One-sentence product-style summary." />
<meta property="og:type" content="article" />
<meta property="og:title" content="Product-style method title" />
<meta property="og:description" content="Specific motivation + mechanism + outcome." />
<meta property="og:image" content="./og-card.svg" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="Product-style method title" />
<meta name="twitter:description" content="Specific motivation + mechanism + outcome." />
<meta name="twitter:image" content="./og-card.svg" />
```

For GitLab Pages or public publishing, replace relative `og:image` with an absolute URL when the final URL is known. The card copy should reuse the hero claim, not a generic document title.
