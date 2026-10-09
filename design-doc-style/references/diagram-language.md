# 图语言：plate / SVG 组件图

来源：`docs/architecture/feishu-bridge/group-sync.html` 图 1、图 2 等，在 12 个既有页面里逐字重复使用的一套约定。这是 [`tokens.css`](tokens.css) 之外单独拆出来的一份，因为图比正文更容易走样——一旦颜色语义在图里和正文不一致，读者会先信图、后信文字。

## 何时画这种图，何时不用

- 承重图（组件关系、状态机、端到端流程）：用本约定的 SVG plate。
- 页面里穿插的小型示意（少于 4 个节点、不需要跨栏）：可以用简单的 flex/grid 卡片代替，不必上 SVG。
- 高保真产品截图/UI mock：不用本文件，见 [`mockup-components.md`](mockup-components.md)。

## 容器：`<figure class="plate">`

```html
<figure class="plate">
  <div class="plate-head"><b>Figure 1 · 一句话图题</b><span>大写关键词 · 大写关键词</span></div>
  <div class="plate-body">
    <svg viewBox="0 0 1320 650" role="img" aria-labelledby="fig1-title fig1-desc">
      <title id="fig1-title">给屏幕阅读器的一句话标题</title>
      <desc id="fig1-desc">给屏幕阅读器的完整文字版描述：把图里的每个框、每条线用一段话说清楚，
        这段话独立于图片也要能让人理解结构——写图注不能偷懒替代这段 desc。</desc>
      <!-- boxes / lines / text -->
    </svg>
  </div>
  <figcaption><b>图 1 附注。</b>补充图上写不下的规则、例外和存疑项；不要重复 desc 里已经说过的话，desc 面向"看不见图的人"，figcaption 面向"看得见图、想知道弦外之音的人"。</figcaption>
</figure>
```

`.plate` 是深色描边容器，`.plate-body` 负责横向滚动（`overflow-x: auto`），SVG 用 `min-width: 1040px` 强制不挤压——图挤在一起比出现横向滚动条更糟。

**`id` 必须每张图唯一**：`fig1-title`/`fig1-desc` 里的 `1` 是图号，不是占位符原样抄写——一页只要出现第二张 plate 图（`fig2-title`/`fig2-desc`……），`aria-labelledby` 和 `id` 都要跟着换号。两张图共用同一个 `fig-title`/`fig-desc` 是最容易在复制粘贴时踩的坑：浏览器不会报错，但 `aria-labelledby` 会指向错的 `<title>`/`<desc>`，读屏软件读到的图 2 标题其实是图 1 的。

## 颜色语义（必须与 [`tokens.css`](tokens.css) 的语义色一一对应）

| CSS 类 | 颜色 | 表示什么 |
|---|---|---|
| `.svg-box` / `.svg-line`（无修饰符） | 灰绿 `#547268` / `#6f8b81` | 中性、内部默认组件 |
| `.svg-box.mint` / `.svg-line.mint` | mint `#66d7b2` | 本图的主体模块、正常路径、"这是核心" |
| `.svg-box.orange` / `.svg-line.orange` | orange `#ff9b61` | 外部依赖里"还没做完/待验证"的那一部分 |
| `.svg-box.blue` / `.svg-line.blue` | blue `#79aefc` | 外部系统、下游消费方、只读依赖 |
| `.svg-box.rose` / `.svg-line.rose` | rose `#ff7f8f` | 阻断路径、失败分支、破坏性动作 |
| `.svg-dash`（叠加在 line 上） | 虚线 `stroke-dasharray: 7 5` | 非自动 / 需要人工介入 / 条件恢复的连线 |

**没有 `.svg-box.violet` / `.svg-line.violet`，这是刻意的**：violet 在 tokens.css 里的语义是"第三方 / 尚未决定"，只用于正文和表格（`.doc-chip.verify`、`.boundary` 第三栏）；"尚未决定"不是图上一个节点或一条线能表达的状态，不要为了凑满 5 个语义色而在 SVG 里发明一个 violet box/line。

一张图里同一个颜色只能代表一件事：例如 `group-sync.html` 图 1 里，橙色专门留给"M1 的接缝"，图 2 状态机里橙色专门留给"容量类阻断态"——不要在同一张图里让橙色一会儿表示外部依赖、一会儿又表示阻断态。落笔前先想清楚这张图要用哪 2-4 种颜色、各自代表什么，再画。

## 文字层级

```css
.svg-title { fill: #edf7f2; font: 700 15px var(--atlas-sans); }  /* 框内标题，每框最多一行 */
.svg-copy  { fill: #a8bdb5; font: 13px var(--atlas-sans); }      /* 框内说明，1-2 行 */
.svg-mono  { fill: #93aaa1; font: 12px var(--atlas-mono); }      /* 框内的字段名/状态值/编号，等宽字 */
```

`.svg-mint` / `.svg-orange` / `.svg-blue` / `.svg-rose` 是纯色版（`fill` 而非 box 背景），用于给文字本身上色，例如强调某个状态名。

## 箭头与连线

用 `<marker>` 定义箭头，每种线色配一个同色 marker（`viewBox="0 0 10 10"`，`refX="9" refY="5"`，三角形路径 `M0 0L10 5L0 10z`）。实线 = 自动发生的路径；虚线 = 人工触发或条件恢复——这条规则要在图例（legend）或 figcaption 里显式写出来，不能让读者自己猜。

## 图例（legend）

复杂图（状态机、多模块组件图）末尾一律加一行文字图例，例如：

> 实线 = 自动 · 虚线 = 条件恢复 / 人工

不要单独设计图形化图例组件——这一套图语言里，图例就是普通的 `.svg-mono` 文字，写在图的角落或 figcaption 里即可，保持轻量。

## 交互模式（可选）：点击节点展开细节

静态图是默认状态——大多数图不需要交互。当一张图的节点数够多、每个节点背后都有值得展开的细节（字段列表、职责说明、依赖状态）、写进 SVG 又会把图挤爆时，才升级到这个模式。

视觉/交互水准参考 [Archify](https://github.com/tt-a1i/archify)（MIT，一个专门做"可探索技术图"的 agent skill）：点节点看细节、追踪关联路径、按需才有动效，静态是默认。但**不要把整份技术方案 / UX PRD 做成 Archify 那样"整页就是一张可探索的图"**——这里的图始终是文档里的一个 `<figure>`，文档本身照常是有 hero、有 section、有表格、有决策记录的正文；交互只服务于"这个图节点太多，点开看细节"这一个具体问题，不要为了炫技而给不需要交互的图也加上去。

### 实现：原生 `<details>` + 一段极小的 vanilla JS

不用任何框架或外部脚本。核心思路：SVG 里的每个可点节点只是一个 `<g data-node="...">`；图的下方紧跟一组 `<details>`，一个节点一份，默认全部收起。点 SVG 节点 = 展开对应的 `<details>` 并把其他收起（同一时刻只聚焦一个），点几下都不会污染页面状态；不点、不启用 JS 时，每个 `<details>` 依然能独立点开——这是原生 `<details>` 的默认行为，不依赖 JS 就能用，这也是选它而不是自己写手风琴组件的原因。

**一页多图时**，每张图与其详情组包在同一个 `.diagram-unit` 中；下一张图开始前结束上一组详情。选择节点时只查询当前 `.diagram-unit`，例如先用 `node.closest('.diagram-unit')` 找范围，再用该范围内的 `[data-node]` 找详情。这样不同图都可以有语义上的 `source` 节点而不会串图。实际 HTML `id`、SVG `<title>`/`<desc>`、箭头 marker ID 仍须全页唯一；`data-node` 只要求在单图内唯一。详情中的字段、职责、证据和验收应补足图的论点，不能只重写节点标签。

多图时将下面单图示例里的全局查询改为逐图查询，详情必须放在所属图的 `<figure>` 之后：

```html
<div class="diagram-unit" data-diagram="overview">
  <figure class="plate">…<g class="node" data-node="source" tabindex="0" role="button">…</g>…</figure>
  <div class="plate-detail-list"><details class="node-detail" data-node="source"><summary>来源</summary>…</details></div>
</div>
<script>
for (const unit of document.querySelectorAll('.diagram-unit')) {
  const nodes = [...unit.querySelectorAll('.plate .node[data-node]')];
  const details = [...unit.querySelectorAll('.node-detail[data-node]')];
  function select(key) {
    const target = details.find(detail => detail.dataset.node === key);
    if (!target || !unit.contains(target)) return;
    details.forEach(detail => { detail.open = detail === target; });
    nodes.forEach(node => node.dataset.node === key
      ? node.setAttribute('aria-current', 'step') : node.removeAttribute('aria-current'));
  }
  nodes.forEach(node => {
    node.addEventListener('click', () => select(node.dataset.node));
    node.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(node.dataset.node); }
    });
  });
}
</script>
```

**"同一时刻只聚焦一个"只是点 SVG 节点这条路径的增强行为，不是全局互斥契约**：读者直接手动点开多个 `<details>`（不经过 SVG 节点）时，浏览器不会自动收起其他项——这是原生 `<details>` 故意保留的行为，符合"不启用/不触发 JS 也完整可用"的降级要求，不需要额外写 JS 去强制互斥。

```html
<figure class="plate">
  <div class="plate-head"><b>Figure N · 一句话图题</b><span>点节点展开细节</span></div>
  <div class="plate-body">
    <svg viewBox="0 0 900 260" role="img" aria-labelledby="fig2-title fig2-desc">
      <title id="fig2-title">…</title>
      <desc id="fig2-desc">…</desc>
      <g class="node" data-node="ingest" tabindex="0" role="button" aria-label="展开 ingest 节点详情">
        <rect class="svg-box mint" x="40" y="80" width="200" height="80" rx="6"/>
        <text class="svg-title" x="140" y="125" text-anchor="middle">ingest</text>
      </g>
      <!-- 其余节点同构 -->
    </svg>
  </div>
  <figcaption>…</figcaption>
</figure>

<div class="plate-detail-list">
  <details class="node-detail" id="node-ingest" data-node="ingest">
    <summary>ingest · 摄取</summary>
    <p>字段、职责、依赖状态……写不进 SVG 的细节都放这里。</p>
  </details>
  <!-- 其余节点同构 -->
</div>

<script>
(() => {
  // Wrap each figure and its details in a .diagram-unit; IDs are unique.
  document.querySelectorAll('.diagram-unit').forEach(unit => {
  const nodes = [...unit.querySelectorAll('.plate .node')];
  const details = [...unit.querySelectorAll('.node-detail')];
  function select(id) {
    const target = document.getElementById(`node-${id}`);
    if (!target || !unit.contains(target)) return;
    details.forEach(d => { d.open = d === target; });
    nodes.forEach(n => n.dataset.node === id ? n.setAttribute('aria-current', 'step') : n.removeAttribute('aria-current'));
    target.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }
  nodes.forEach(n => n.addEventListener('click', () => select(n.dataset.node)));
  nodes.forEach(n => n.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(n.dataset.node); } }));
  details.forEach(d => d.addEventListener('toggle', () => {
    if (!d.open) nodes.filter(n => n.dataset.node === d.dataset.node).forEach(n => n.removeAttribute('aria-current'));
  }));
  });
})();
</script>
```

CSS（加进 `tokens.css` 已有这部分，这里只说规则）：`.node[aria-current="step"] .svg-box` 用更亮的描边/发光表示"当前聚焦"；`.plate-detail-list` 里的 `<details>` 复用 `.table-wrap` 同款深色描边卡片，不要另设一套配色。

### 追踪路径（进阶，仍然可选）

节点数再多一步、且图里的连线本身就是论点的一部分时（例如"数据从哪流到哪"），可以在选中节点时同步给相关的 `.svg-line` 加 `.trace-active`：

```css
.svg-line.trace-active { stroke-width: 2.4; }
@media (prefers-reduced-motion: no-preference) {
  .svg-line.trace-active { stroke-dasharray: 6 4; animation: trace-flow 900ms linear infinite; }
}
@keyframes trace-flow { to { stroke-dashoffset: -20; } }
```

动效永远是"选中态才有"，不做背景常驻动画；`prefers-reduced-motion: reduce` 时必须回退成纯高亮无动画（上面的写法已经这样处理）。

### 打印 / 无 JS 兜底

```css
@media print {
  .plate-detail-list details > *:not(summary) { display: block !important; }
}
```

打印或导出 PDF 时，所有 `<details>` 内容强制显示，不管 `open` 属性——评审经常打印出来看，交互态在纸上没有意义，必须退回"全部展开"的完整静态版本。

## 校验清单

- [ ] `<svg>` 有 `role="img"` 且 `aria-labelledby` 指向 `<title>` + `<desc>`
- [ ] 同一页面有多张图时，每张图的 `fig<N>-title` / `fig<N>-desc` 都换了号，没有两张图共用一个 id
- [ ] `<desc>` 完整复述图的结构，不依赖颜色也能看懂（色盲/黑白打印场景）
- [ ] 同一颜色在全文（正文语义色、SVG、图例）里只代表一件事
- [ ] 实线/虚线的含义在图上或 figcaption 里写明
- [ ] `figcaption` 补充"存疑/例外"，不复述 `desc`
- [ ] `.plate-body` 允许横向滚动，SVG 没有被压扁到文字重叠
- [ ] 用了交互模式时：不启用 JS 也能逐个点开每个 `<details>`；关掉 JS 或打印时不丢失任何信息
- [ ] 多图交互时：总览先于专题图；每张图的详情紧接该图；相同 `data-node` 不会跨图串开；每个可点节点都有对应详情
- [ ] 用了追踪动效时：`prefers-reduced-motion: reduce` 下动画消失、只剩静态高亮


## 图—目录—内容导航

每张交互图独立组成 `.diagram-unit`：图回答关系，**图下的左 panel 是节点目录，右 panel 是单项详情**。它不是整页侧栏，也不是目录跳到下方堆叠的折叠列表。整页章节目录只定位章节，和这个局部目录分开。

```html
<div class="diagram-unit">
  <figure class="plate"><!-- SVG 节点 data-node="map-node" --></figure>
  <div class="plate-detail-list detail-explorer">
    <nav class="node-directory" aria-label="节点目录">
      <a href="#detail-map-node" data-detail="detail-map-node" aria-controls="detail-map-node">节点名称</a>
    </nav>
    <div class="detail-reader">
      <details open class="node-detail" id="detail-map-node" data-node="map-node" tabindex="-1">
        <summary>节点名称</summary><div><!-- 实际职责、输入/输出、证据和 Gate --></div>
      </details>
    </div>
  </div>
</div>
```

- 将 [detail-explorer.css](detail-explorer.css) 与 [detail-explorer.js](detail-explorer.js) 完整内联。图节点与详情 ID 必须全页唯一，脚本只操作所属 `.diagram-unit`。首项默认选中；真实 hash 优先于默认。
- 点击图节点选中右侧详情并定位**所属详情区**。点击局部目录只切换右侧内容和焦点，不使整页跳动。两者同步 `aria-current="step"`。右侧滚动回顶部；目录与阅读区各自滚动、保持高度稳定。
- 图节点支持 Enter/Space；目录原生链接支持 Enter，并可用上下箭头/Home/End 切换。目标模块 `tabindex="-1"`，程序切换后聚焦详情；方向键保留目录焦点。减少动态效果时用即时定位。
- 全部详情预置在 HTML，JS 启用后只有当前详情可见，不能按需从远端取回正文。无 JS 时原生 `<details open>` 全部可读。打印展示全部正文，取消阅读区高度/滚动，打印后恢复选中项。
- 窄屏将局部目录放在阅读区上方，仍保持单项详情与稳定高度。整页不得横向溢出，图可以局部横向滚动。
- 内容保留真实并行、反馈、输入输出、Owner、证据与待验证项；目录编号只表示阅读顺序，不把并行依赖误画成串行流程。
- 图旁目标/案例栏可用 `details name` + 范围内 toggle 做互斥展开，详情在栏内滚动，不撑高图；打印取消限制。它的功能不同于节点详情，不能替代右侧正文。

[完整内联示例](diagram-outline-example.html) 验证局部双栏、单项详情、图/目录同步、键盘、hash、无 JS 与打印。发布前用实际中文长内容和窄屏验收。
