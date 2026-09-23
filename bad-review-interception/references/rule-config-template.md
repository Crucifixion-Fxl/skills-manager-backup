# 拦截规则配置模板

> Step ③ 配规则时 PM 需要填的字段清单。缺任一字段都不能上线——每个字段都有对应的事故/教训在背后。

## 必填字段

| 字段 | 示例 | 为什么必填 |
|---|---|---|
| **trigger_event** | `dwd_event_flutter_home_bind_fail_hi` | 事件表全名，精确到 dwd 层 |
| **trigger_error_code** | `10001` | 精确到 code，不允许"任何 error_code 都触发"——Rule 2 |
| **trigger_condition** | `count(bind_fail_hi) >= 2 within 5min` | 触发阈值：单次失败还是 N 次重试后。单次阈值会过度骚扰 |
| **user_symptom** | 绑定设备一直转圈卡住 | 客服视角，用户能自己描述的话 |
| **tech_root_cause** | 路由器 mDNS 广播未开启 | 研发视角，用于后续定位和根除 |
| **self_help_steps** | 1. 检查路由器 mDNS<br>2. 关闭 AP 隔离<br>3. 退出 App 重进 | ≤3 步，每步都是用户自己能做的事。如果任何一步需要技术背景（如"检查 WebSocket 连接"），该规则不可发布 |
| **fallback_action** | 跳转客服工单表单 | 必填，Rule 3 |
| **frequency_cap** | 同一用户 24h 内最多 1 次 | 防骚扰。弹窗频率是用户厌恶的主要来源之一 |
| **target_app_versions** | `>= 6.2.0` | 低版本 App 可能没有对应的弹窗容器 |
| **scene** | `binding` | 关联 Step ④ 的 proximal metric——Rule 5 |
| **proximal_metric** | `binding_success_rate` | scene 级的 A/B 核心指标 |
| **ab_bucket_initial** | `5% treatment / 95% control` | 灰度起点 |
| **growthbook_flag** | `intercept_binding_10001` | feature flag 名字，Step ④ 查显著性用 |

## 多语言 copy（Payload CMS）

通过 `marketing-cms` skill 在 Payload CMS 里维护。默认需要覆盖以下 8 种语言（走 Crowdin 翻译）：

- `en` — English
- `de` — Deutsch
- `es` — Español
- `fr` — Français
- `it` — Italiano
- `ja` — 日本語
- `zh-Hans` — 简体中文
- `zh-Hant` — 繁體中文

每个语种需要填：
- `title` — 弹窗标题（≤20 字符建议）
- `body` — 弹窗正文（≤80 字符建议）
- `cta_primary` — 主按钮（进入自助流程）
- `cta_secondary` — 次按钮（"暂不需要" / 兜底客服入口）

## 上线前 checklist

- [ ] PM 已填齐上表所有必填字段
- [ ] 客服已 review `self_help_steps`，确认步骤可行、话术友好
- [ ] `proximal_metric` 已在 Superset 里有对应的 dashboard 面板可查
- [ ] GrowthBook 实验已创建，分桶比例是 5%
- [ ] 所有必填语种的 Payload CMS 条目已发布
- [ ] 规则字节码（`.evc`）CI 打包成功并上传到 S3 staging
- [ ] App 在模拟器上手动验证过一次弹窗能正常显示+跳转

任何一条未打勾都不能进入 50% 灰度。
