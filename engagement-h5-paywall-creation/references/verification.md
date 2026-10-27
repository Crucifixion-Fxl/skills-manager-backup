# H5 Paywall 验证矩阵

## 分层结论

| 证据 | 证明什么 | 不能代替什么 |
|---|---|---|
| lint/build/unit | 分层、配置绑定、注册目录、规则/组件行为 | 部署、视觉审批、Native 支付 |
| Playwright/Mock Bridge | 交互、WebView 尺寸、报价/购买调用契约 | 真实商店、支付、权益、数仓 |
| Figma candidate/差异 | 指定 frame 的可复查视觉差异 | 设计验收、真机交互 |
| 制品目录/CMS 发布回读 | 对应链路在目标环境存在 | 其他环境或生产已上线 |
| 真机 Sandbox/Test | Native、实际渠道报价、支付/权益 | 生产发布/支付 |
| 看板/数仓 | 事件真实入库和归因 | 像素正确 |

缺失写 NOT_RUN 或待验证项，不用其他层证据代替。

## Figma 高保真

1. 固定 file key、node ID、读取时间、采用区域/尺寸；指定 Design by DW 时不混用其他版本。
2. 映射完整页面/关键状态：主推荐、试用/付费选中、All Plans/挽留、loading/error、弹层；没有设计稿的状态按现有交互契约并注明来源。
3. 对下述适配矩阵的每个组合固定 viewport、DPR、profile、语言、字体、安全区；检查背景、字体、颜色、布局、间距、卡片/按钮/页脚与滚动。
4. 动态金额可局部 mask，不整体遮盖周期、折扣、选中态、CTA 或价格区域。
5. 输出全屏/区域 candidate、差异与未还原项。阈值通过不代替高保真判断，契约通过不等于视觉验收。
6. 不自动覆盖 approved baseline，明确确认后才更新。

PRD/用户已确认的交互优先于设计中冲突的行为，保留差异说明；未决定的冲突需补齐输入，不能静默猜测。

## 头图与多屏适配验收

先记录设计要求：页面是否满宽、内容边距、头图比例、允许的裁切区域，以及头图与标题/购买区的关系。画板宽度（如 375 CSS px）是对照基准，不自动成为所有手机的固定页面宽度；明确要求限宽或留白的设计按其约束验收。

| 范围 | 验收要求 |
|---|---|
| 屏幕矩阵 | 按目标 App 的设备范围选取设计基准、比基准窄和宽的视口，并包含短屏与长屏；例如 320×568、375×667、390×844、430×932 CSS px，可按实际范围调整并记录依据，不能只改变高度。受影响布局在 WebKit、Chromium 中均执行。 |
| 头图与容器 | 满宽设计的页面容器对齐实际可用视口，头图对齐容器两侧；没有意外白边、拉伸或裁剪。等比图保持比例；设计允许裁切时核对主体与文字仍在可见区域，不以隐藏横向溢出来掩盖错误宽度。 |
| 内容与滚动 | 保留设计规定的内容边距、字号和控件尺寸，不用整页缩放消除白边；无意外横向滚动。短屏上商品与操作可滚动到达、不被购买区遮挡，固定购买区按设计保持可见。 |
| 状态 | 选择会改变头图或下方布局的代表状态，例如默认套餐、切换套餐、有/无试用提醒；复用既有语言与安全区矩阵，不重复维护另一套测试框架。 |
| 版本兼容 | 从目标 App/依赖声明核对支持的 iOS/WKWebView 与 Android WebView 范围。新增 CSS 能力（如 `aspect-ratio`）须核实支持范围；不覆盖目标版本时使用兼容布局或经验证的回退，不能让属性失效后头图占位塌缩、标题重叠或按钮不可达。 |

在现有业务 suite 中先补失败用例再修复：断言实际视口、容器与头图边界、比例/预期裁切、横向溢出、内容可达和购买区位置，并保留设计基准尺寸回归。允许记录明确的亚像素容差，不以 CSS 字符串匹配代替渲染结果。缺失 CSS 能力的模拟可用于回归，但须标明模拟条件，不能据此宣称旧版本真机通过；未取得的目标版本证据列为待验证项。

截图交付沿用上述矩阵和 Figma candidate 流程：

- 字体、图片解码及该状态所需报价渲染稳定后，保存原尺寸全屏截图；需要时补头图区域或滚动后截图。加载失败保留为失败状态，不当作完成态验收。
- 修复类任务保留同一 viewport、DPR、安全区、状态与浏览器版本下的修复前后对照；记录实例 ID、源 SHA、素材、mock/live 来源及截图路径。
- 交付可按平台、尺寸和状态查看的截图索引，逐项标注白边、裁剪、溢出、遮挡检查结果及未验证项。尺寸断言通过后仍需检查实际截图，不能只交日志或仅交设计宽度截图；视觉 review、baseline 批准与真机验收仍按上面的证据分层处理。

## 自动化

按 checkout 脚本和指南，新模式通常执行：

```bash
cd paywall-h5
npm run lint
npx vitest run
npm run build
npm run test:e2e:instances
```

再运行业务交互/视觉 suite；旧模板运行 npm test、test:e2e:contract 和对应 suite。lint 已含 architecture/tracking 时不重复。测试失败修复或报告，不删用例、弱化门禁或批准 baseline 放行。

业务 E2E 必须复用 checkout 已有的集中 Playwright 配置与 npm/CI 命令。先执行目标 suite，再通过 `npm run test:e2e:ci -- --list` 或目标 checkout 的等价 selection 命令核对新增 spec/标签确实被 CI 选中；流水线绿色但 list 中没有业务用例不能作为覆盖证据。普通实例不得新增 `playwright.<business>.config.ts`、私有 dev-server 端口或 runner；需要新增集中 family/config 时走 Owner 评审并覆盖 selection 测试。

基础 fixture 只证明基础契约，不代表任何正式业务 Figma 页面完成。

## 新实例必要矩阵

| 范围 | 必查项 |
|---|---|
| 身份/构建 | 唯一 ID、tenant、config/definition、目录；保留 ID 不被占用 |
| ID 互斥 | staging/prod CMS 业务 ID/key 与新实例 ID 不重名；核对 checkout 与已部署 H5 实例，记录来源/结果，不能只依赖受保护 ID 或构建通过 |
| 初始化 | 命中走 runtime；缺事实/商品、失败/重试、旧请求隔离 |
| 兼容 | 未命中走 CMS；命中失败不切 CMS；旧标准/个性化行为保持 |
| 选品 | DUAL/MULTI、历史代际/周期/资格、未知事实、默认 offer key |
| 身份 | 同 SKU 不同 Offer、计费类型/周期、参考非购买、报价/购买一致 |
| 渠道 | 默认/显式 payType（含0）、报价/购买/法律一致、不静默切换 |
| 状态 | 选择、loading/partial/error/retry；使用 quotedOptions/canPurchase；失败默认项不自动换购；公共 overlay/结果 |
| 操作互斥 | 同一批次重复点击及购买/关闭/恢复/提醒/重试竞争只执行许可动作；失败提示与显式重试；作用域切换/卸载后旧结果不回写 |
| 导航 | 主/挽留/All Plans 独立选择/恢复，close/No Thanks/legal/restore |
| 投放 | 上游最终 ID、不重新路由分群/实验；paywallSource=h5_instance/runtime=h5；直接入口及 blocks CTA 的目标环境校验 |
| 显示 | 按[头图与多屏适配验收](#头图与多屏适配验收)覆盖双端多屏宽、长短屏与版本兼容；结合长文案/多语言/安全区/可访问性 |
| 本地化 | 全部目标语言的实际资源/key/占位符完整；非英文整页无意外英文回退；计费模板/周期/复数本地化；主/挽留/All Plans/错误态及法律入口均检查，见 [localization.md](localization.md) |

多语言核对全部要求 locale 的 key / 命名占位符与英文源一致；语言别名（如 zh_CN）、非支持语言英文回退、ar/he RTL 有用例。代表语种长文案无横向裁切；检查 role、label、键盘/焦点与稳定 UI 自动化标识。旧模板额外验证其代码侧 locale 与 CMS 可配字段分别覆盖。

测试需求启用的能力，共享 Controller 变化回归实际调用方，不逐实例复制底层状态机内部测试。商品实验复用公共报价/交易；私有 UI 复制不允许导致两份交易逻辑。检查 changed files：普通实例不得修改公共基础/门禁，边界见 [template-boundary.md](template-boundary.md)，能力接入见 [reusable-capabilities.md](reusable-capabilities.md)。

## 埋点与访问生命周期

用现有 adapter/helper 验证公共语义组件 PV、区域曝光、CTA/No Thanks/retry、报价/购买 payload、取消/失败/成功和触点归因连续性。

Page/config/scene 出现原始 event、Bridge 方法、手拼公共字段或绕过 public 的网络，是分层问题，不只补测试放行。新事件由 Owner 评审公共语义。

当目标 checkout 已接入公共 `usePaywallVisit`，对现有标准 CMS、个性化 CMS 和独立实例各取至少一个代表页面，验证同一公共契约；新页面不复制 recorder 或手发事件。

| 场景 | 预期 |
|---|---|
| 主动单指纵向滚动后退出 | 先发一次 `page_scroll`，保存本次访问最大 `scroll_screens` 与 `reached_bottom`，再发一次 `page_exit` |
| 未滚动退出 | 不发 `page_scroll`，只发一次 `page_exit` |
| 程序化滚动、横向轮播、支付弹窗、鼠标/键盘 | 不产生页面滚动指标 |
| H5 显式关闭、Android 系统返回、iOS 已完成返回手势 | 结算一次；重复关闭/Native 通知不重复上报 |
| App 后台、普通遮盖、iOS 取消返回手势、React cleanup/load error | 不按主动退出结算；后台时间不计入前台停留 |
| 支付成功后关闭/跳转 | 已发生滚动仍结算 `page_scroll`，不发 `page_exit` |
| 使用已接入的公共挽留 action 进入挽留页 | 先结算当前访问；从挽留页返回后新建访问并重新发 PV；实例 public 无此语义 action 时记 Owner 缺口，不从 scene 导入 recorder |
| All Plans/普通内部弹层 | 默认仍属于当前访问，不额外创建 PV/退出；产品要求独立指标时先评审新的访问边界 |

同时核对 `page` 来自当前页面 `spmb`，`paywall_runtime=h5`，触点/实验/来源字段沿访问开始时快照保留，`page_exit` 的 `exit_type=back`、`impl=h5` 与前台 `duration_ms` 符合已发布口径。事件字段或含义变化不能由业务页面自行扩展。

证据分三层记录：H5 unit/DOM/Playwright 证明滚动识别、结算和 Bridge 顺序；Android/iOS 真机证明系统返回、已提交/取消手势与可见性通知；目标环境数仓/看板证明事件入库、去重和归因。Mock Bridge、H5 制品或单端 SDK 任一项都不能单独证明完整线上覆盖。旧 App SDK 缺少 Native 通知时，显式 H5 关闭可能通过，但系统返回/手势必须标记 `NOT_RUN` 或不支持。

## 环境与真机

新模式核对目标 H5 制品、instance-catalog.json、Admin 对应环境目录 URL 和 ID；Backend evaluate 不请求 H5 静态目录，规则不保存实例 tenantId/spmb 副本。blocks 入口还需核对其 CMS 素材在目标环境已发布。旧模式核对发布 CMS/模板，staging 不证明 production。

部署后按启用范围验证：

- iOS/Android 页面、字体、素材、滚动、安全区与关闭。
- App 切换目标语言后固定文案与动态值正确，RTL 方向正确，记录译文审批状态。
- 本地化报价、Product/Offer、周期、provider sheet 与购买参数一致。
- 每个渠道取消不产生权益、成功一次且可回读权益、失败可恢复。
- 真实触点按时间窗、slot_name、paywall_id 核对既有漏斗归因。

凭证沿安全 UAT 流程，不入代码/截图/MR。区分 IMPLEMENTED_NOT_RELEASE_APPROVED、STAGING_UAT_PASSED 与实际发布/生产验收；页面实现不能推出已上线。
