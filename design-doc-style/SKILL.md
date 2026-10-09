---
name: design-doc-style
description: 复用一套已在生产文档里验证过的自包含 HTML 视觉语言（design tokens + SVG 图语言 + 高保真界面模拟组件），用于写技术方案 / 架构设计 / UX PRD 这类"重设计"文档，避免每篇从零发明配色或手抄漂移。触发词："技术方案文档""设计文档要好看点""参考 XX 项目的文档风格""画状态机图""画组件图""UX PRD""逐屏图册""首页风格""文档站统一风格""design token""图语言"。当用户已经在用 HTML-first 写架构/产品文档（如 addx:architect 的产出）、只是想要更强的视觉表达力时使用；不要用于普通 Markdown 文档或不需要图的简单说明。
---

# design-doc-style

## Description

一套可以直接复制粘贴复用的深色杂志感视觉语言，同时提供首页叙事、目录与节点详情的共享组件，专门给"技术方案文档 / UX PRD"这类需要图、需要状态呈现、需要让人一眼分清优先级的重设计文档用。它解决的问题不是"能不能画图"，而是同一批文档反复手抄同一套配色和组件、抄着抄着就在变量命名和结构上悄悄分叉——本 Skill 把已经验证过的那份钉成标准件。

## 什么时候用

- 正在写技术方案 / 架构设计 / UX PRD，内容量到了需要分 Part、需要状态机图、需要给决策项标优先级的程度。
- 已经确定用自包含 HTML 承载（例如遵循 `architect` skill 的 HTML-first 约定），只是想要比纯 Markdown 表格更强的视觉表达。
- 想复现"某个项目的技术方案文档"那种深色杂志质感，而不想每次都重新设计一遍配色和组件。

**不适用**：日常 Markdown 笔记、ADR（ADR 按公司规范固定用 Markdown，不套视觉皮肤）、不含图表的简单说明文档。

## Rules

1. **单文件、全内联，零外部引用**：产出物永远是一个可以独立打开的 `.html` 文件——所有 `<style>`、`<script>` 都内联在这一个文件里；不 `<link>` 外部 CSS/JS、不接公网 CDN、不用 `<iframe>`、不引用相对路径的图片或字体。这是硬约束，不是偏好：文档要能在 VS Code 内置预览、沙箱、离线、GitLab Pages 等任何环境下都正常渲染，外链资源在这些环境里经常取不到。可运行的完整示例见 [`example.html`](example.html)（本身就是单文件，用浏览器或 VS Code 预览直接打开即可看到效果）。
2. **两层皮肤，缺一不可**：`<head>` 里的 `<style>` 内容分两层——第一层是共享浅色基座（表格、正文排版这些通用结构，通常仓库里已有，可能由脚本生成，不要手改；没有的话用 [`references/base-skeleton.css`](references/base-skeleton.css) 起步），第二层是本 Skill 的深色皮肤（[`references/tokens.css`](references/tokens.css)）。两层都要整段复制内联，不要只抄一部分就假设另一层"应该已经有了"。
3. **颜色只有 5 个语义色，不额外发明**：mint=正常/主线、orange=需要人处理/待验证、blue=外部依赖、rose=破坏性/阻断、violet=待决。前 4 个在正文、SVG 图、UI mock 里必须用同一套映射；violet 只用在正文/表格（`.doc-chip.verify`、`.boundary` 第三栏），"尚未决定"不是图或 UI 能表达的状态，SVG 和 mockup 组件都不需要 violet 版本——不要为了凑满 5 个而硬造。
4. **图比文字更容易走样**：状态机图、组件关系图一律走 [`references/diagram-language.md`](references/diagram-language.md) 的 `plate` 约定（容器、颜色语义、`<title>`/`<desc>` 无障碍标注、图例），不要每张图重新发明一套颜色和箭头样式。
5. **图的交互水准参考 Archify，但文档不是 Archify**：节点数多、每个节点都值得展开细节的图，可以按 [`references/diagram-language.md`](references/diagram-language.md) 的"交互模式"加点击节点展开详情（原生 `<details>` + 一小段内联 vanilla JS，无 JS 也能独立点开每个细节块）、以及选中态的路径追踪高亮——这是把 [Archify](https://github.com/tt-a1i/archify) 那种"可探索技术图"的交互标准，缩小范围用在文档里的单张图上。**不要**把整份技术方案 / UX PRD 做成"整页就是一张可探索大图"——文档主体仍然是 hero、section、表格、决策记录这些常规结构，图只是嵌在其中的一个 `<figure>`；静态图仍是默认，只有图本身撑不下所有细节时才升级到交互。
6. **UX PRD 的逐屏图册用高保真组件，不用截图**：交互文档需要"这一屏长什么样"时，用 [`references/mockup-components.md`](references/mockup-components.md) + [`references/mockup-components.css`](references/mockup-components.css) 里的 `.adm`/`.wz`/`.fk`/`.dlg` 这套浅色产品窗口模拟组件画在深色文档背景上，而不是插入真实截图（截图会过时、无法在评审时改文案）或纯散文描述（读者要在脑内拼装界面）。
7. **每张图、每一屏都要能钉回正文**：SVG 图的 `figcaption` 和逐屏图册的注解，必须写清楚"为什么这样设计"并指回验收标准 / 状态机的具体状态值；界面不能展示技术设计里没有的状态。
8. **命名不要再分叉**：CSS 变量统一用 `--atlas-*` 前缀（见 tokens.css 顶部说明），历史上出现过不加前缀的另一种命名方言，颜色值相同、纯粹是命名对不齐——新文档不要再造第三种命名，也不必回头改历史页面。
9. **长段正文优先可读性**：callout 内的标题、正文和行内 `code` 必须连续排版，不能因容器 `display: grid` 把术语拆成整行色块。长段落所在的表格、边界卡和 hero note 使用实底与正文色；`--atlas-muted` 留给短标签和次要元数据。深色主题必须显式覆盖基座的 `.callout.info` 背景，否则其浅蓝底会因选择器优先级保留下来。复制模板后用实际长中文段落在浏览器中检查屏幕与打印对比度。
10. **多图文档按目标逐层展开**：先用一张总览图回答目标、边界和主要责任，再用独立的图分别解释数据来源、工作流和变更传播。每张图只承载一个问题，正文按同样顺序给出决定、验收与未验证项；不要把所有细节塞进一张总图，也不要把图堆成无叙事的图册。
11. **节点详情紧跟所属图**：交互图的 `<figure class="plate">` 后必须立即放该图自己的 `.plate-detail-list`，在进入下一图或章节前给读者看完对应详情。每个可点击节点都要有一个匹配的 `<details>`；跨图使用唯一节点标识，并把点击逻辑限制在所属图的容器内，避免同名节点打开另一张图的详情。图下详情必须写实际职责、输入/输出、证据或验收，不重复图上标题。无 JS、键盘和打印时仍能读到全部详情。

12. **图下详情用局部双栏**：每张图下面的 `.plate-detail-list.detail-explorer` 左侧是**这张图的节点目录**，右侧是**当前选中节点的完整详情**。图点击和局部目录都切换右侧内容；整页章节目录另管章节导航，不能拿它替代节点目录。详情全部预置于 HTML；JS 启用后仅显示一项，目录和阅读区独立滚动、保持高度稳定；无 JS 与打印恢复全部详情。真实 hash、键盘与焦点规则见 [图—目录—内容](references/diagram-language.md#图目录内容导航)，完整示例见 [局部详情示例](references/diagram-outline-example.html)。
13. **首页先叙事，再展示**：首页每个章节回答一个问题，用大标题、一句解释、一个独立演示区与详情入口。两张图回答不同问题时拆成独立段落，不放在一个大文档框里。同一导航下有文化、方法、实践、规范时，下拉按内容类型分区，并提供一个使用相同目录数据的独立总览页。方法、Owner、Gate 和证据的长文本进详情页；不能通过截断或隐藏原文冒充首页精简。参考 [首页与文档站模式](references/storytelling-patterns.md)。
14. **整站统一，首页与正文保持不同密度**：统一颜色、字体层级、边框、焦点态与节点交互，覆盖首页、Catalog、Loop 详情和文化页。保留默认墨绿皮肤；需要沿用 Skill Hub 夜色时，在完整基座与 tokens 后内联 [夜色变体](references/hub-theme.css)，它只调整表面色与排版，保留 mint/orange/blue/rose 的语义。独立设计文档仍须单文件内联；仓库发布的多页产品首页可复用随仓库分发的本地资产，禁止依赖公网 CDN。
15. **动效解释状态，不延迟阅读**：可使用 Motion / Framer 做视口渐入、步骤切换、反馈路径；初始内容可读，手动选择立即响应，自动演示可暂停，离开视口停止，减少动态效果时保持静态交互。独立 HTML 使用内联 CSS/JS；多页站点可用本地 vendored runtime。打印移除动效、隐藏目录并完整展开详情，不裁剪固定高度区域。

16. **业务 Loop 正文按阅读层次展开**：首屏一句主线、图先于长解释；节点详情以短动作和 Owner / 证据 / Gate 字段组织。完整目的、指标口径、护栏、交接、能力缺口与引用放入原生可展开说明，全部保留在 HTML 中；不用 CSS 裁剪替代精简，不把折叠态当信息已删除。无 JS 能原生展开，打印打开所有层级的 details 并在打印后恢复原状态。

## 用法

1. 看一眼 [`example.html`](example.html)——完整、可独立打开的成品示例，比读规则更快建立直觉。
2. 检查所在仓库是否已有共享浅色基座；没有就复制 [`references/base-skeleton.css`](references/base-skeleton.css)。再复制 [`references/tokens.css`](references/tokens.css)，两段一起内联进新文档同一个 `<style>` 块；给 `<div class="page">` 加上 `atlas-page` 类。
3. 按内容体量选结构：短文档只需要 `.hero` + 若干 `section`（`.section-head` + `.section-no` + `.section-copy`）；长文档（多个 Part）在 Part 之间插入 `.part-divider`。产品首页使用 Rules #13 的低密度叙事；目录和文化页共用 Rules #14 的主题。
4. 需要论证结构关系的图 → 读 [`references/diagram-language.md`](references/diagram-language.md)，按 `plate` 容器 + 颜色语义画 SVG；节点多到需要展开细节时，按同一文件的"交互模式"加局部节点目录与右侧详情（原生 `<details>` + 内联 vanilla JS，见 Rules #5）。
   多张交互图时按 Rules #10/#11 逐图展开：总览 → 专题图；每张图下立即放它自己的详情组。
5. 需要展示界面交互的图册（UX PRD）→ 读 [`references/mockup-components.md`](references/mockup-components.md)，复制配套的 [`references/mockup-components.css`](references/mockup-components.css)，按 `.shot-frame` 外框 + `.adm`/`.wz`/`.fk` 骨架拼装。
6. 决策类表格用 `.table-wrap` + `.thesis`（一段话论点）+ `.boundary-grid`（范围内/外/待定三分类），不要把决策淹没在大段散文里。
7. 发布前对照 diagram-language.md 末尾的校验清单过一遍：颜色语义是否统一、SVG 是否有无障碍标注、交互模式是否在无 JS / 打印时仍完整可读、深色底在 `@media print` 下是否会整页打印成黑色。再检查一处带行内 `code` 的长段 callout，确认文字连续、背景与正文足够清楚。

## Examples

### ❌ Bad

#### 1. 外链资源

```html
<head>
  <link rel="stylesheet" href="tokens.css">
  <script src="https://cdn.example.com/tiny-helper.js"></script>
</head>
```

**问题分析：**
- `<link>` 外部样式表在沙箱预览、离线打开、VS Code 内置预览这些环境里经常取不到，页面会变成没有样式的裸 HTML
- 接公网 CDN 违反"单文件、全内联"的硬约束（Rules #1）
- 正确做法是把 `tokens.css` 的内容整段复制进同一个文件的 `<style>` 里，见 [`example.html`](example.html)

#### 2. 颜色语义不一致 + 无障碍标注缺失

```html
<svg viewBox="0 0 400 200">
  <rect x="20" y="20" width="150" height="60" fill="#ff9b61"/>
  <text x="95" y="55">外部依赖</text>
  <rect x="220" y="20" width="150" height="60" fill="#ff9b61"/>
  <text x="295" y="55">已阻断</text>
</svg>
```

**问题分析：**
- 同一个 orange 在这张图里同时表示"外部依赖"和"已阻断"两件不相干的事，违反"一张图里同一个颜色只能代表一件事"（diagram-language.md）
- 没有 `role="img"`、`<title>`、`<desc>`，读屏软件拿不到图的内容，色盲或黑白打印场景下这张图完全不可读
- 颜色是手写的十六进制值而不是 `.svg-box` 语义类，换一次主题色就要全图逐个改

### ✅ Good

#### 1. 全部内联 + 语义类 + 完整无障碍标注

```html
<head>
  <style id="site-css">/* base-skeleton.css 内容 */</style>
  <style id="feature-css">/* tokens.css 内容 */</style>
</head>
<body>
  <figure class="plate">
    <div class="plate-head"><b>Figure 1 · 请求处理路径</b><span>SOLID = 自动 · DASHED = 人工</span></div>
    <div class="plate-body">
      <svg viewBox="0 0 400 200" role="img" aria-labelledby="fig1-title fig1-desc">
        <title id="fig1-title">请求先过校验，再进入外部依赖，失败则阻断</title>
        <desc id="fig1-desc">校验节点（mint）通过后进入外部依赖节点（blue）；
          校验失败走虚线进入阻断节点（rose），需要人工处理。</desc>
        <rect class="svg-box blue" x="20" y="20" width="150" height="60" rx="6"/>
        <text class="svg-title" x="95" y="55" text-anchor="middle">外部依赖</text>
        <rect class="svg-box rose" x="220" y="20" width="150" height="60" rx="6"/>
        <text class="svg-title" x="295" y="55" text-anchor="middle">已阻断</text>
      </svg>
    </div>
    <figcaption><b>图 1 附注。</b>阻断态只在校验失败时出现，恢复条件见 §04。</figcaption>
  </figure>
</body>
```

**优点：**
- 两层 `<style>` 都内联在同一个文件里，任何环境打开都是完整样式，符合 Rules #1 / #2
- `blue`（外部依赖）和 `rose`（阻断）各表示一件事，且用的是 tokens.css 里定义好的 `.svg-box` 语义类，不是手写颜色值
- `role="img"` + `<title>` + `<desc>` 齐全，读屏软件和黑白打印都能理解这张图；`figcaption` 只补充 `desc` 没讲的存疑项，没有重复

#### 2. 图是文档的一部分，不是整份文档

```html
<div class="page atlas-page">
  <main>
    <header class="hero">…</header>          <!-- 正文照常有 hero -->
    <section id="scope">…</section>           <!-- 正文照常有 section、表格、决策记录 -->
    <section id="flow">
      <figure class="plate">…</figure>        <!-- 图只是嵌在 section 里的一个组件 -->
    </section>
  </main>
</div>
```

**优点：**
- 图的交互水准可以参考 Archify，但整份文档仍然是 hero / section / 表格 / 决策记录这些常规结构，不会退化成"打开就是一张可探索大图"（Rules #5）

## 参考

- [`example.html`](example.html) —— 完整可运行的单文件示例：hero、part-divider、决策表格、交互式 plate 图（点节点展开细节）、一屏 UX mock，全部内联
- [`references/base-skeleton.css`](references/base-skeleton.css) —— 没有共享基座时的最小起点（page 网格、表格、callout 等结构性规则）
- [`references/tokens.css`](references/tokens.css) —— 可直接复制的 design tokens 与页面级组件（hero / callout / table / boundary-grid / thesis / part-divider / plate 图 / 交互高亮）
- [`references/diagram-language.md`](references/diagram-language.md) —— SVG `plate` 组件图的容器、颜色语义、无障碍标注、图例约定，以及可选的点击展开交互模式
- [`references/mockup-components.md`](references/mockup-components.md) + [`references/mockup-components.css`](references/mockup-components.css) —— UX PRD 逐屏图册用的高保真界面模拟组件（管理后台 / 向导 / 飞书消息 / 对话框）

来源与实例：`gitlab.addx.ai/infra/buzz-deploy` 的 `docs/architecture/feishu-bridge/group-sync.html`（技术方案 · 组件图 + 状态机图）与 `docs/product/group-sync/channel-feishu-group-ux.html`（UX PRD · 13 屏图册），以及同仓另外 10 个复用同一视觉语言的页面；交互模式的水准参考 [Archify](https://github.com/tt-a1i/archify)（MIT），落地实现参考原生 `<details>` 点击展开这一通用 Web 模式（不依赖 Archify 代码本身）。
