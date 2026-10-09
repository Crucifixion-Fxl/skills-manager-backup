# 高保真界面模拟组件（UX PRD 专用）

来源：`docs/product/group-sync/channel-feishu-group-ux.html`（13 屏图册）、`docs/product/agent-identity/agent-identity-sync-ux.html`。这套组件解决的问题：UX PRD 里逐屏讲交互，如果只用散文描述"卡片上有群名、群主、成员数"，读者要在脑内拼装界面；用真实像素级的浅色产品窗口画在深色文档里，读者一眼就认出"这是管理后台""这是飞书私信"。

这套组件只用于 UX PRD / 交互设计文档；纯技术方案文档（组件图、状态机、数据模型）不需要，用 [`diagram-language.md`](diagram-language.md) 就够。

**这是静态视觉稿，不是可交互原型**：`.pbtn`、`.fk-btn`、`.tick` 这些看起来像按钮的元素故意用 `<span>` 而不是 `<button>`/`<a>`——它们不需要可点击、不需要键盘可达，因为它们只是"这一屏长什么样"的图片替代品，不是给读者操作的真实界面。不要指望复制这段 HTML 就能拿到一个能跑的原型；真的需要可交互草图时用专门的原型工具，本 Skill 管的是"文档里怎么画出一屏截图"，不管"怎么做出一个能点的 demo"。

**CSS 在 [`mockup-components.css`](mockup-components.css)**：整段复制进文档的 `<style>` 块，和 [`tokens.css`](tokens.css) 放在一起即可，不要外链引用——本 Skill 的所有产物都必须是单文件自包含 HTML（同一份 `.html` 里内联全部 `<style>`，不 `<link>` 外部样式表、不接公网 CDN、不用 `iframe`），这样文档才能在任何环境（VS Code 内置预览、沙箱、离线打开、GitLab Pages）下都正常渲染。可运行的完整示例见 [`example.html`](../example.html)。

## 外框：`.shot-frame` → `.shot-grid` → `.shot`

外框元素必须是 `<figure>`——`.shot-frame` 只是样式类，不是语义容器；`<figcaption>` 只有在 `<figure>` 内部才是合法的图注，才会被读屏软件正确关联，也才会被将来任何 `figure figcaption` 选择器命中。不要把 `.shot-frame` 挂在 `<div>` 上再让 `<figcaption>` 独立飘在外面。

```html
<figure class="shot-frame">
  <div class="frame-label"><b>管理平台 · 频道详情</b><span>active · Owner 视角</span></div>
  <div class="shot-grid solo">      <!-- 单屏用 "solo"，两屏对比用 shot-grid 不加 solo -->
    <div class="shot">
      <div class="shot-bar">
        <span class="shot-dots"><i></i><i></i><i></i></span>
        <span class="shot-url">admin.example.internal/channels/product-design</span>
        <span class="shot-tag">频道详情</span>
      </div>
      <!-- 具体界面内容：.adm / .wz / .fk 三选一做主体骨架；需要弹窗时再叠加下面的 .ovl+.dlg 或 .fc -->
    </div>
  </div>
  <figcaption><b>屏幕编号 注解。</b>解释这一屏的设计判断——为什么这样呈现、和哪条验收标准对应。<b>追溯：</b>UX-CFG-xx · 黑盒 ID。</figcaption>
</figure>
```

`.shot` 本身是浅色产品窗口（`background:#fff; color:#1f2329`），画在深色文档背景上，靠 `box-shadow` 和圆角制造"悬浮窗口"的效果——这是刻意的对比设计，不要把 `.shot` 也做成深色。`.shot-bar` 模拟浏览器地址栏，`shot-url` 放假的内部域名，不放真实可访问的地址。

## 三种主体骨架（三选一）

每一屏选一种作为骨架；需要弹窗/遮罩时，在骨架内容之上再叠加下一节的 `.ovl`+`.dlg` 或 `.fc`（弹窗不是第四种骨架，是叠加层）。

| 类名 | 模拟什么 | 何时用 |
|---|---|---|
| `.adm` | 管理后台（左侧深色导航 + 右侧内容） | 展示"状态卡""列表页"这类管理界面 |
| `.wz` | 向导（顶部步骤条 + 卡片内容 + 底部操作栏） | 展示多步流程（绑定向导、审批向导） |
| `.fk` | 飞书私信 / 群消息 | 展示机器人通知、私信卡片、群系统消息 |

### `.adm`（管理后台）

```html
<div class="adm">
  <aside class="adm-side"><b>A4x</b><span>概览</span><span class="on">Channels</span><span>审计</span></aside>
  <div class="adm-main">
    <div class="adm-title"><b>#product-design</b><small>私有频道 · Owner jchen · 14 名成员</small></div>
    <div class="panel">
      <h5>飞书群 · <span class="chip bound">同步中</span></h5>
      <div class="rd-row"><span class="rd-name">群</span><span class="rd-note">产品设计讨论</span><span class="pbtn pri sm">在飞书中打开</span></div>
    </div>
  </div>
</div>
```

`.rd-row` 是最小信息行：左边字段名、中间说明文字、右边一个状态 chip 或按钮。需要引导用户注意的那一行加 `rd-hot`（虚线描边高亮）。

### `.wz`（向导）

```html
<div class="wz">
  <div class="wz-top"><span class="wz-title">把「产品设计讨论」绑定到 Buzz 频道</span><span class="wz-sub">确认前不会有任何改变</span></div>
  <div class="wz-steps">
    <div class="wz-step done"><i></i><span>① 身份</span></div>
    <div class="wz-step on"><i></i><span>② 选择频道</span></div>
    <div class="wz-step"><i></i><span>③ 预览</span></div>
  </div>
  <div class="wz-body">
    <div class="banner warn"><b>将从群里移出 3 人。</b>他们不在 #product-design 里。</div>
    <div class="wz-card">…</div>
  </div>
  <div class="wz-foot"><small>说明文字</small><span><span class="pbtn ghost">上一步</span><span class="pbtn danger">确认并移出 3 人</span></span></div>
</div>
```

步骤条状态只有三种：未开始（无修饰符）、`on`（当前）、`done`（已完成）——不要发明"跳过"之类的第四态。`.banner` 的四色（`ok/warn/bad/info`）对应正文的语义色，破坏性预告一律用 `warn` 或 `bad`，绝不用 `ok`。

### `.fk`（飞书消息）

```html
<div class="fk">
  <div class="fk-head"><span class="fk-ava">桥</span><span><b>Buzz Bridge</b><br><small>机器人 · 平台通知</small></span></div>
  <div class="fk-body">
    <div class="fk-msg"><span class="fk-ava">桥</span>
      <div class="fk-bubble">
        <span class="fk-title">已为 #product-design 创建飞书群</span>
        <span class="fk-line">群名「产品设计讨论」，<b>你是群主</b>。</span>
        <div class="fk-actions"><span class="fk-btn">打开群</span><span class="fk-btn ghost">查看频道</span></div>
        <span class="fk-foot">每个群只通知一次 · 09-17 10:20</span>
      </div>
    </div>
  </div>
</div>
```

每条模拟消息末尾用 `fk-foot` 写清楚频控（"每个群一次""同一原因 24 小时内最多一条"）——这是 UX PRD 里最容易漏写、也最容易被评审问到的细节，写文案样例时顺手把频控标出来。

## 对话框 / 遮罩：`.ovl` + `.dlg`，或 `.fc`

这是叠加层，不是第四种骨架——两者选一种叠在 `.adm`/`.wz`/`.fk` 之上，不会同时出现在同一屏。

**`.ovl` + `.dlg`**：普通确认/决策对话框（居中卡片 + 半透明遮罩），用于"这个操作要不要继续"这类场景：

```html
<div class="ovl"><div class="dlg">
  <h6>停止 #product-design 与飞书群的同步？</h6>
  <ul><li><b>保留</b>：飞书群和现有成员、聊天记录。</li><li><b>停止</b>：成员不再跟随频道增减。</li></ul>
  <div class="fk-actions"><span class="pbtn ghost">取消</span><span class="pbtn pri">停止同步</span></div>
</div></div>
```

非破坏性的确认（如本例"停止同步"，随时可重新开启）用 `pbtn pri`（品牌色）主按钮；真正不可逆或影响他人的动作用 `pbtn danger`（红色）主按钮——按钮颜色本身就是给读者的风险信号，不要为了视觉好看把危险动作也配成 `pri`。

**`.fc`**：更轻量的一次性授权/首次连接卡片（品牌小图标 + 一句话 + 权限范围说明），用于"要不要把 A 和 B 账号连起来"这类首次授权场景，比 `.dlg` 更聚焦、不带列表：

```html
<div class="fc"><div class="fc-card">
  <div class="fc-brand"><i>B</i><span>Buzz Bridge 请求访问</span></div>
  <h6>把这个群和 Buzz 频道同步？</h6>
  <div class="fc-scope"><b>将获得：</b>读取群成员列表、按频道成员增删群成员</div>
  <div class="fk-actions"><span class="pbtn ghost">不需要</span><span class="pbtn pri">同意</span></div>
</div></div>
```

`.dlg` 用于"决策"（有列表、有取舍），`.fc` 用于"授权"（讲清楚拿到什么权限就够了）——两者不要混用，一屏只需要其中一种。

## 原子组件速查

| 类名 | 用途 |
|---|---|
| `.chip.bound/warn/bad/absent/info/done` | 状态徽标，语义映射与正文一致（bound=正常、warn=警示、bad=错误、absent=中性灰、info=信息）；色值是浅色 UI 专用的独立调色板，不直接复用 `--atlas-*` 变量的值——见文件顶部说明 |
| `.pbtn.pri/blue/ghost/danger` | 按钮：品牌色主按钮 / 次要品牌色 / 幽灵按钮 / 危险按钮 |
| `.tick.ok/run/todo/bad` | 检查项列表：已通过 / 进行中 / 待办 / 未通过 |
| `.banner.ok/warn/bad/info` | 页内横幅提示，四色对应正文语义色 |
| `.ttl` / `.ttl.bad` | 小型状态标签（黄色警示 / 红色错误），用在行内 |

**没有 violet 版本的 chip/tick/banner，这是刻意的**：violet 在 [`tokens.css`](tokens.css) 里的语义是"第三方 / 尚未决定"，用于正文表格和 `.doc-chip.verify`；UI 状态天生只有"正常/警示/错误/无关/进行中"这几种，没有"尚未决定"这个 UI 状态，所以 mockup 组件的调色板只覆盖 mint/orange/rose/blue 加两个中性灰（`absent`/`done`），不需要也不应该强行造一个 violet chip。

## 写这类图册时的判断原则

- **样本数据要具体**：用真实感的人名、频道名、数字（"12 人已在群里，2 人还没绑定"），不要写"用户 A""若干成员"——具体数字能暴露设计里没想清楚的边界情况。
- **每屏一个注解 + 一条追溯**：注解讲这一屏"为什么这样设计"（价值判断），追溯把这一屏钉死到验收标准 ID 和技术设计的状态值——界面不能发明技术设计状态机里没有的状态，这是最常见的一类评审意见。
- **失败态和边界态不能省**：至少要有一屏"这条路走不通时看到什么"（如本文的 B5 拒绝页），不要只画 happy path。
