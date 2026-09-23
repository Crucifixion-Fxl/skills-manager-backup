# HTML-first 架构文档写作参考

本文是 `architect` 的 HTML 架构正文方法 SSOT。`SKILL.md` 只保留默认规则；当需要创建、重构或 review 面向人的架构正文时，按本文执行。这里的方法来自 `device-event-mesh` 已落地的 HTML 文档体系：`docs/index.html`、`docs/architecture/*.html`、`docs/architecture/diagram-style-guide.html` 和 `scripts/ensure-dm-palette.py`。其中既有 HTML ADR 只作为历史兼容样例，不再代表新 ADR 格式。

> 规则边界：`SKILL.md` / `references/*.md` 是 skill 包自身的载体，不属于被 `/architect` 生成的项目文档。`/architect` 在业务项目里新增或重写的**架构正文**默认是 HTML；新 ADR 是固定例外，必须按 [`adr-format.md`](adr-format.md) 写成 Markdown。`docs/design/`、`docs/requirements/` 等生命周期文档仍按 `SKILL.md` 的读者与 diff 需求选 md 或 html。

## 1. 适用范围

默认使用 HTML 的**面向人架构正文**：

- 架构总览、system overview、component overview、vertical cockpit。
- 架构范围内的 domain model、glossary、接口/事件契约、deployment topology、capacity sizing 与 observability design。
- 解释系统结构所需的数据流、写入/读取路径、状态机、故障恢复、容量测算和架构 dashboard。

产品调研、PRD、User Story、用户手册、测试执行、release 进度和 implementation plan 不由本文规定格式，分别遵守其 owning Skill / 生命周期目录。尤其 `docs/requirements/<iid>/plan.md` 是 `architect/SKILL.md` 的固定 Markdown 产物，不能因本文的 HTML-first 架构正文规则改成 HTML。

必须使用 Markdown 的文档：

- 新 ADR 与 ADR 索引：`adrs/NNNN-*.md` + `adrs/README.md`。
- ADR 的候选方案、trade-off、Decision Outcome 和 Consequences 都写在同一 `.md` 文件。
- 既有 HTML ADR 保留原路径和历史，不批量迁移；不得据此继续创建新 HTML ADR。

## 2. 从 device-event-mesh 抽出的最佳实践

| 实践 | 规则 | 来源形态 |
|---|---|---|
| 站点总入口 | `docs/index.html` 先给系统上下文、阅读路径、文档依赖图、ADR 列表；读者不用猜从哪开始。 | `docs/index.html` |
| 专题页 | 每个复杂主题单独 HTML，正文围绕一个问题闭环，不把所有内容塞进一个长页。 | `docs/architecture/*.html` |
| ADR 决策集 | 每条新 ADR 是一个结构固定的 Markdown 文件；`adrs/README.md` 做机器可读索引；状态、日期、相关 ADR、候选方案、trade-off、后果使用固定字段和章节。 | `docs/architecture/adrs/*.md` |
| 图语言 SSOT | 全站使用 `.dm-*` 语义类：颜色表示角色、形状表示类别、线型表示流向；不要每页随意配色。 | `diagram-style-guide.html` |
| 关键样式内联 | 即使链接 `site.css` / `adr.css`，使用 `.dm-*` 的页面也要内联关键调色板，防止沙箱预览剥离外部 CSS 后图变黑块。 | `scripts/ensure-dm-palette.py` |
| 页内大纲 | 长 HTML 必须有固定大纲/导航，桌面侧栏、移动端折叠，标题有稳定锚点。 | `doc-outline` |
| 图注和图例 | 每张承重图必须有 `aria-label`、`.dm-figcap` 和 `.dm-legend`，让图离开正文也能被读懂。 | `diagram-style-guide.html` |
| 决策可追溯 | HTML 架构页摘要决策并链接 Markdown ADR；ADR 反链相关专题页和上下游 ADR。 | `docs/architecture/adrs/*.md` |
| 验证脚本 | 对图语言做可执行校验；没有脚本时至少跑浏览器/截图检查和 `git diff --check`。 | `ensure-dm-palette.py` |

## 3. 推荐工作流

1. 读输入：PRD/User Story、已有 ADR、`catalog-info.yaml`、现有 HTML docs、关键代码路径。
2. 画文档地图：列出每类问题的 SSOT，区分 HTML 架构正文与 Markdown ADR，明确旧路径仅作兼容导航。
3. 定入口：`docs/index.html` 或 `docs/architecture/index.html` 作为总览/阅读路径；专题页放 `docs/architecture/<topic>.html` 或 catalog-aware 的 `docs/architecture/<system>/index.html` / `<component>/<topic>.html`。
4. 写骨架：先放业务上下文、读者路径、系统边界、核心图、ADR 引用、细节导航，再补正文。
5. 画承重图：复杂流程用 SVG，少量节点的辅助图可用 Mermaid，但最终 HTML 里必须能自包含渲染。
6. 写 ADR：按 `references/adr-format.md` 创建 Markdown ADR 和 `adrs/README.md` 索引。
7. 验证：浏览器或截图检查可读性；跑项目已有 HTML/diagram 校验脚本；最后跑 skill/repo 校验。

## 4. 页面契约

每个 HTML 文档页必须满足：

| 维度 | 要求 |
|---|---|
| 自包含 | 不依赖外部网络资源；可以链接仓内 `site.css`/`site.js`，但关键图样式必须能在外部 CSS 被剥离时仍渲染。 |
| 语言 | 默认中文，保留必要英文术语和代码名；领域术语链接到 `domain-model.html` 或 glossary。 |
| 导航 | 有当前页大纲、返回总览、相关文档/ADR 链接。 |
| 阅读路径 | 总入口说明先读什么、再读什么、哪些是参考层。 |
| 可视化 | 至少一个承重图；复杂数据流/状态流不能只靠散文。 |
| 可访问 | SVG 有 `role="img"` 和清楚的 `aria-label`；图下有一句话图注。 |
| 响应式 | 移动端不重叠、不截断关键文字；宽图可横向滚动或自适应。 |
| 决策链接 | 每个架构结论能追到 ADR、代码、契约或测试 SSOT。 |
| 可验证 | 有本地校验命令；使用 `.dm-*` 的项目建议提供 `scripts/ensure-dm-palette.py docs/ --check`。 |

## 5. 推荐页面结构

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>主题 · 项目名</title>
  <link rel="stylesheet" href="site.css">
  <script defer src="site.js"></script>
  <style>
    /* 页面局部样式；若使用 .dm-* 图语言，在这里内联关键调色板 */
  </style>
</head>
<body>
  <main>
    <details class="doc-outline" open>
      <summary>本页大纲</summary>
      <nav aria-label="本页章节">...</nav>
    </details>

    <nav aria-label="文档导航">...</nav>
    <header>
      <p class="eyebrow">Architecture</p>
      <h1>主题名</h1>
      <p class="lead">一句话说明本页回答什么问题。</p>
    </header>

    <section>
      <h2>1. 业务上下文与边界</h2>
    </section>

    <section>
      <h2>2. 系统架构</h2>
      <svg role="img" aria-label="...">...</svg>
      <p class="dm-figcap">图 1 · 一句话解释图。</p>
      <ul class="dm-legend">...</ul>
    </section>

    <section>
      <h2>3. 核心流程</h2>
    </section>

    <section>
      <h2>4. 决策与 ADR</h2>
    </section>

    <section>
      <h2>5. 细节导航</h2>
    </section>
  </main>
</body>
</html>
```

## 6. SVG 图语言

优先沉淀项目级语义类，而不是每页随意配色。`device-event-mesh` 的有效模式是 `.dm-*`：

| 语义 | 类名 | 用途 |
|---|---|---|
| 控制面 / 共识 | `.dm-ctl` | Raft、etcd、fencing、epoch gate |
| 计算 / 处理 | `.dm-compute` | ingest、worker、compactor、scheduler |
| 热数据 | `.dm-hot` | L0、RocksDB、hot index、local state |
| 冷存储 | `.dm-cold` | object storage、L1/L2、manifest |
| 成功 / 健康 | `.dm-ok` | commit、ready、checksum match |
| 风险 / 门控 | `.dm-gate` | reject、GC gate、overload、危险窗口 |
| 云 / 容器 | `.dm-cloud` | region、cluster、node group、neutral box |
| 设备 / 客户端 | `.dm-device` | mobile、device、SDK、本地投影 |

线型规则：

- `.dm-flow`：数据流，实线。
- `.dm-ctlflow`：控制流 / fencing，虚线。
- `.dm-async`：异步 / 跨域复制，虚线。

图形规则：

- 矩形表示服务/组件/进程。
- 圆柱表示持久化存储。
- 菱形表示决策、门控、不变量校验。
- 泳道表示参与方或时序角色。

每张图必须有唯一 marker id 前缀，避免同页多图箭头冲突。

## 7. visual-documentation-skills 全量引用

`visual-documentation-skills` 是可借鉴的结构化图文方法库。`architect` 写项目文档时必须按场景参考其中全部 skill，但不要直接套通用模板或渐变风格；优先使用项目既有 `site.css`、`.dm-*` 语义色、中文术语、ADR 链接和真实代码路径。

| plugin skill | 何时参考 | 在 /architect 中怎么落地 |
|---|---|---|
| `visual-documentation-skills:architecture-diagram-creator` | system overview、high-level architecture、deployment、data flow | 用它的 business context / data flow / processing pipeline / system architecture / deployment 六段思路，但图形语言改成项目 `.dm-*`。 |
| `visual-documentation-skills:technical-doc-creator` | API、developer docs、代码示例、工作流 | HTML 中保留 API reference、request/response、code block、workflow；代码块自包含，不依赖外部 highlight CDN。 |
| `visual-documentation-skills:flowchart-creator` | process flow、decision tree、状态机、发布/故障恢复路径 | 用泳道、菱形门控、带标签箭头表达分支；复杂状态流必须图文并列。 |
| `visual-documentation-skills:dashboard-creator` | SLA、容量、成本、监控、风险雷达、测试覆盖矩阵 | 用 KPI 卡、表格、SVG bar/line、progress indicators 表达指标；数值必须有来源和口径。 |
| `visual-documentation-skills:timeline-creator` | roadmap、implementation plan、migration、release plan | 用阶段、里程碑、依赖、准入/退出条件表达计划；不要把进度流水账混进设计 SSOT。 |

## 8. HTML 架构页与 Markdown ADR 的边界

项目新 ADR 必须是 Markdown：

- 文件名：`docs/architecture/**/adrs/NNNN-<kebab-slug>.md`。
- 索引：同目录 `README.md`，列出编号、决策、status、date、supersedes/superseded-by。
- metadata：YAML front matter 固定包含 `status`、`date`、`deciders`、`supersedes`、`superseded-by`。
- 决策结构：Context and Problem Statement、Considered Options、Trade-off Analysis、Decision Outcome、Consequences。
- HTML 架构页承担人读可视化，只保留决策摘要和 `.md` 链接，不复制 ADR 正文。
- 决策史：不删除旧 ADR；新 ADR supersede 旧 ADR，旧记录状态改为 Superseded 并反链新记录。
- 历史 `.html` ADR 原地保留；不要求批量迁移，但也不得作为新 ADR 模板。

完整模板和兼容规则见 [`adr-format.md`](adr-format.md)。

## 9. Review checklist

提交前检查：

- HTML 页是否回答“是什么、谁负责、何时触发、走什么机制、禁止什么、去哪看细节”。
- 页面是否链接相关 ADR、domain、contracts、testing、代码路径。
- 复杂流程是否有 SVG / 状态机 / 时序图，而不是只有散文。
- 图是否有 `aria-label`、图注、图例、稳定语义色。
- 页面是否无外部网络依赖、移动端无重叠、长文本不溢出。
- 使用 `.dm-*` 的页面是否内联关键调色板；如有脚本，运行 `python3 scripts/ensure-dm-palette.py docs/ --check`。
- 新 ADR 是否是 `.md`、front matter 和标准模板章节是否齐全，并已同步 `adrs/README.md`；历史提升 ADR 和索引漂移的 MR 严重级别按 `code-review` 判定。
- HTML 架构页是否只摘要并链接 ADR，没有复制决策正文。
- 既有 HTML ADR 是否保留路径和历史；没有无批准批量迁移，也没有新增 HTML ADR。
- 运行 `git diff --check`；如果是 skill 变更，运行 `python3 scripts/validate.py --skill skills/architect`。

## 10. 示例对比

### Bad

```text
写一个 docs/architecture/index.md 充当面向人的架构总览
只放一张 Mermaid 图和几段结论
把多条 ADR 聚合到 gateway-adrs.html，正文又复制进架构页
没有阅读路径、图例、SSOT 链接、移动端检查
```

### Good

```text
docs/index.html 说明阅读路径和文档依赖
docs/architecture/<topic>.html 承载系统边界、核心 SVG 流程、风险门控和细节导航
docs/architecture/adrs/NNNN-*.md 记录决策推理
docs/architecture/adrs/README.md 维护机器可读 ADR 索引
HTML 页链接 Markdown ADR / domain / contracts / testing，只摘要不复写
```
