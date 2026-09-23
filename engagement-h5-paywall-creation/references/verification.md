# H5 Paywall 验证矩阵

## 分层结论

| 层级 | 证明什么 | 不能证明什么 |
|---|---|---|
| Unit / component | UI 映射、状态与可访问性 | 浏览器布局、Native、真实支付 |
| Playwright | WebView 尺寸下的 UI、交互和 Mock Bridge 契约 | 商店真实价格、支付、权益、数仓入库 |
| Staging CMS | 发布配置、模板选择和内容读取 | 生产发布正确 |
| 真机 Sandbox / Test | Native Bridge、provider sheet、取消 / 成功、权益 | 生产支付 |
| Superset / 数仓 | 事件真实入库和漏斗连续 | UI 像素正确 |

任何层级缺失，都要在最终结果中写 `NOT_RUN`，不能写成 `PASS` 或用别层证据代替。

## Figma 视觉验证

1. 固定 Figma file key、node ID、读取 / 导出时间和目标 frame 尺寸。
2. 建立 frame 到业务场景的映射，包括分群、设备档位、主页面、All Plans、弹窗和错误态。
3. 固定 Playwright viewport、DPR、浏览器 / WebView profile、语言、字体探针和安全区。
4. 动态价格可以 mask，但折扣标签、周期、商品选中态和 CTA 状态不能被整体遮盖。
5. 先生成 candidate，再输出全屏及关键区域差异。阈值只防明显回归，不代表设计审批。
6. 未经人工确认，不执行 `--update-snapshots` 覆盖已批准 baseline。

若 Figma 与明确的业务交互决定冲突，记录差异并让业务 / Owner 选择；不要静默选择其中一个。

## 浏览器与契约

基础命令：

```bash
cd paywall-h5
npm run lint
npm test
npm run build
npm run test:e2e:contract
```

按模板运行已有 suite，并检查至少以下状态：

| 范围 | 必查项 |
|---|---|
| 初始化 | CMS loading、成功、缺内容、失败与 retry |
| 价格 | loading、完整、部分、空、失败、延迟 |
| 商品 | 默认选择、切换、周期、Offer ID、按钮可用状态 |
| 页面 | 关闭、No Thanks、法律页、支持页、恢复购买 |
| capability | All Plans、trial reminder、purchase target 等 manifest 声明行为 |
| 支付 | 开始、取消、失败、成功、校验失败和结果 UI |
| 平台 | iOS 与 Android viewport、安全区、长文案 |
| 可访问性 | role、label、键盘 / 焦点、稳定 UI 自动化标识 |

模板测试要断言用户可见行为和 Template Kit 语义动作，不测试或复制内部支付状态机实现。

## 埋点与 Bridge

产品运营不设计基础事件。验证框架是否自动产生既有序列：

- 页面 PV；
- 普通信息区、商品区和购买区曝光；
- CTA / No Thanks / retry 等已有语义点击；
- 价格请求与购买 payload 使用当前选中的 Product ID 和 Offer ID；
- 支付取消、失败与成功结果；
- 从触点进入时，支付成功保留触点和 Paywall 归因。

读取 `window.__PAYWALL_MOCK__.calls` 或现有 Playwright helper 对比调用序列。模板中出现原始 event name、Bridge method 或手拼公共字段，应直接判为越界，而不是补测试放行。

## Staging 与真机门禁

Staging 部署后至少完成：

1. CMS 已发布内容可被目标 paywall key 读取，templateKey 和商品矩阵正确。
2. iOS 与 Android 真机打开正确页面，字体、图片、滚动、安全区和关闭行为正常。
3. Native 返回真实本地化价格，选择不同商品时 provider sheet 与购买 payload 一致。
4. 对每个启用支付渠道验证取消不扣款、不发权益；成功支付只发生一次并能读回权益。
5. 支付失败和网络失败可恢复，不产生重复订单或重复成功 UI。
6. 从 Engagement 触点进入时，使用通用 P0 看板按时间窗、`slot_name`、`paywall_id` 验证完整漏斗。

真实支付、账号和凭证只通过现有安全 UAT 流程使用，不写入 skill 输出、代码、截图说明或 MR。

## 发布判定

| 状态 | 条件 |
|---|---|
| `IMPLEMENTED_NOT_RELEASE_APPROVED` | H5 代码、CMS Draft、浏览器和契约通过，但未完成真机 / 支付 / 权益 / 看板 |
| `STAGING_UAT_PASSED` | Staging 真机、所有启用渠道、权益和埋点验证通过 |
| `READY_FOR_OWNER_RELEASE_REVIEW` | Staging UAT 证据完整，回滚与影响范围明确，等待 Owner 决定发布 |

Skill 不输出“已上线”或“生产可用”。只有实际 Transfer / 部署完成并经生产验证后，才能由发布流程给出该结论。
