# 测试、可观测性与发布清单

## 目录

- 执行顺序与自动化测试
- GrowthBook/会员/真机状态矩阵
- 客户端埋点与后端可观测性
- 发布、回滚和最终报告

## 1. 推荐执行顺序

1. 确认 PRD/Figma、实际 capability boundary、会员可见策略和 key 草案。
2. 本地完成代码、单测和 contract 测试。
3. 在 Crowdin feature branch 完成全语言；拉取并生成客户端 l10n。
4. 在 staging CMS 创建、Publish 并验证内容 API；生产发布时再按 staging -> pre -> prod Transfer，不把 Publish 当作同步。
5. 调用 `$ninedata` 创建并执行 NH catalog DML SQL 任务，插入 `lab_feature` 行；回查并保存 task ID/URL。
6. 创建/验证 GrowthBook eligibility 与 policy，仅启用授权的 staging 环境。
7. 已有功能扩展到新 App 时，先确认目标 tenant/bundle/version eligibility 已命中，再发布客户端 allowlist；不要制造“默认 ON 可见、显式 OFF 后消失”的中间态。
8. 部署 PE/NH/IOT/CMS 相关服务；确认健康和配置已传播。
9. 从最新 release 基线构建客户端并安装真机，核对 `pubspec.yaml`、`pubspec.lock` 和 Nexus archive 确实包含本次 package 版本。
10. 完成 API、服务端副作用、会员/非会员和 UI 状态矩阵。
11. 验证 5 个实验室平台事件、NH/PE Prometheus/Grafana、数仓链路，以及具体功能的测试日志/Trace。
12. pipeline、Sonar、review 全绿后整理 staging 结论。
13. 用户明确批准后再准备生产配置、数据任务和 prod MR。

数据库前置顺序：NH `lab_feature` 等待 Naturehood migration 自动建表后再申请 NineData DML；PE `user_feature_settings` 等目标 database/user Ready 后申请 NineData DDL，不等待 PE 部署自动建表。staging 当前使用 shared-middleware `Database`，prod-us 当前使用 PE 自有 Crossplane RDS；两者都不自动建业务表。PE 不插初始化用户数据。

顺序可以按依赖并行，但不要在 CMS 内容不存在时让目录对用户可见，也不要在后端门禁未部署时只发布客户端隐藏逻辑。

## 2. 自动化测试

### PE

- 无 `lab` row -> `enabledLabFeatureKeys=[]` 且 `disabledLabFeatureKeys=[]`。
- 一个 row 多个 feature 节点，`enabled=true` 只进入 enabled 数组，`enabled=false` 只进入 disabled 数组。
- malformed/非 boolean 节点不进入任一数组，并按现有错误契约处理。
- ON 创建/更新节点，OFF 保存 false，其他节点不丢失。
- 并发更新不同 feature 不覆盖。
- header/body user ID 不一致拒绝。
- schema source 是 `mysql/personalization_engine`。

### NH

- catalog 映射、排序、CMS/Crowdin key 正确。
- list/check/update 对 policy 状态的矩阵正确。
- 无显式节点时 `feature.enabled` 等于 policy 最终 `runtimeEnabled`，首次 list 不写 PE。
- 默认 ON 首次关闭懒写 false；默认 OFF 首次开启懒写 true；目标值与当前计算状态相同时不 patch。
- `status` 切换不影响运行结果。
- unknown/missing/malformed/PE error fail closed。
- 每个 mutation/query/projector 在禁用时符合契约。
- timeline 多功能使用 batch check，不产生逐卡 N+1。

### IOT/异步服务

- 原有 device/entitlement/business 条件仍有效。
- runtime true 才 enqueue/生成/写结果。
- runtime false、timeout、malformed、PE 5xx 的副作用调用数为 0。
- graduated true 可运行；off false 停止。
- 不存在 IOT -> NH 调用和 raw settings 读取。

### 客户端

- unknown key 默认为 false。
- toggle 成功立即更新 store，失败回滚 UI 并显示既有错误。
- description 在 OFF 时仍显示。
- CMS SVG/PNG、dark fallback、缺失内容行为正确。
- title/description resolver 和全部语言通过测试。
- 实际入口渲染、callback、route/deep link 都受控。
- 多个功能独立切换，关闭 A 不影响 B/C。
- native host 和 Flutter module 版本契约一致。
- 子集客户端只展示 allowlist 内功能，summary/red dot 不被其他功能污染。
- allowlist 外 update 在发请求前被拒绝，旧缓存/深链不能绕过。
- 已有功能扩展到新 App 后，默认 ON/OFF、显式 OFF 后仍可见、再次 ON 和真实入口均按新矩阵工作；不得沿用扩展前的旧 allowlist 断言。
- 产品撤回某 App 支持时，该 App allowlist、真实入口和目标 release package 同步收回；不为从未属于该 App 的能力修改 GB。验证 list/summary/update/深链 fail closed；独立后端入口如存在，则验证产品 capability guard。
- 品牌专属 icon 只影响目标 flavor，共享 CMS 和其他客户端不变。
- `LabFeatureModule` 负责 route、DI、notice bridge 和 dispose；G0 host 没有重复的 Lab-specific 初始化。
- Native 使用 `lattice://lab-features`，已接入实验室的 App 新增单个 feature 时没有无意义的 Android/iOS 页面复制。
- 发布后的 Naturehood/Lattice packages 与 G0 Flutter `pubspec.lock` 都来自 Nexus 同一批新版本，不含 path/git 临时依赖。

## 3. GrowthBook 状态矩阵

| 场景 | eligibility | 显式用户节点 | policy 期望 | 开关 | 实际能力 |
|---|---:|---:|---|---|---|
| active 默认 ON 新用户 | true | 无 | active,true,true | 可见且 ON | ON，不落用户行 |
| active 默认 ON、未配置 hard-deny | false | 无 | active,true,true | 仍可见且 ON | ON；证明 eligibility 不会限制无条件默认 ON |
| active 默认 ON 已关闭 | true | false | active,false,true | 可见且 OFF，可重新 ON | OFF，保存 false |
| active 默认 ON 已关闭但新 App eligibility 漏配 | false | false | active,false,true | **错误：隐藏且无法重新 ON** | OFF；应阻断发布并补 eligibility |
| active 默认 OFF 新用户 | true | 无 | active,false,true | 可见且 OFF | OFF，不落用户行 |
| active 默认 OFF 已开启 | true | true | active,true,true | 可见且 ON | ON，保存 true |
| active 灰度回滚 | false | true | active,true,true | 保持可见 ON | ON |
| active 不符合且未选择 | false | 无 | active,false,true | 隐藏 | OFF |
| paused 历史已开 | 任意 | true | paused,true,false | 可见，可 OFF | ON |
| paused 未开 | 任意 | 无/false | paused,false,false | 不允许 ON | OFF |
| graduated | 任意 | 任意 | graduated,true,false | 隐藏 | ON，仍需原权益 |
| off | 任意 | 任意 | off,false,false | 隐藏 | OFF，无副作用 |
| malformed/missing | 任意 | 任意 | 无有效 policy | 隐藏/不可开 | OFF |

另测 rule order：运行时 hard-deny（如有）必须在用户规则之前，显式 OFF 必须先于显式 ON，环境默认规则必须在最后；更具体 deny/allow/rollout 不得被宽泛规则遮蔽。条件中的用户字段使用 PE 实际传入的 `userId`。每次变更后读取 SDK payload 或 PE eval 结果作为证据。

staging 至少使用“无该功能显式节点”的账号完成两轮矩阵：

1. 所有目标功能默认 ON，首次进入时全部开关为 ON，PE 不新增节点。
2. 一个功能默认 ON、其余默认 OFF，首次进入时状态逐项一致，且功能之间互不影响。
3. 默认 ON 若声明了受众限制，使用 eligibility=false 且命中 hard-deny 的账号验证不可见/不可用；未声明 hard-deny 时明确记录其覆盖所有 policy 消费者。
4. 每轮都实际切换至少一个默认 ON 和一个默认 OFF 功能，验证首次差异值懒写及同值重试不 patch。
5. 测试后恢复 staging 经评审的约定终态，不把临时规则遗留给其他测试。
6. 每个新增支持的 App 单独执行一次，不得用 Kiwibit 命中结果代替 Vico Nature 等其他 tenant/bundle；记录 eligibility、policy 和 NH list 三层原始结果。

## 4. 会员矩阵

每个目标客户端至少使用一个当前有效会员（包含最新 tier）和一个非会员 staging 账号。凭据从团队批准的安全凭据源或用户提供的安全位置读取，不写入仓库、skill 或日志。

| eligibility 策略 | 会员 | 非会员 |
|---|---|---|
| 全用户展示 | 都能看到实验室开关；能否实际使用仍走原权益 | 能看到开关；使用付费能力时按既有 paywall/鉴权 |
| 仅会员展示 | 可看到并按 policy 操作 | 实验室 list 中隐藏 |

对 premium 功能同时验证“实验 policy 允许但 entitlement 拒绝”和“entitlement 允许但实验 policy 拒绝”，防止两层条件被误合并。

## 5. 真机手工测试

- 设置页实验室入口 icon、红点、返回路径和空白页回归。
- 实验室列表与 Figma：icon、排序、标题、OFF 简介、switch 状态、长文案、RTL、深色模式。
- ON 后回到真实业务页，无重启即可看到入口并完成核心任务。
- OFF 后真实入口/受控内容消失或不可用，直接 API/深链也被拒绝。
- 会员和非会员按产品规则显示三点、paywall 或其他公共 chrome。
- 网络错误、PE/CMS 超时、快速连点、离线恢复和重新登录。
- Android 返回栈、进程恢复、崩溃；必要时 iOS 做同等回归。
- 多客户端逐一验证：设置入口、只显示各自 allowlist、品牌图标、红点和开关；不得只用 KB 结果代替 VN。

任何需要产品主观确认的视觉项或必须依赖真实鸟类事件/设备数据的路径，明确列给用户手工验证；其余可自动化的项不要推给用户。

## 6. 客户端埋点

调用 `$tracking-lifecycle`，先审计现有事件再设计。推荐触点，最终 point 和层级以当前应用规范为准：

| 触点 | 关键业务参数 |
|---|---|
| 设置页入口曝光 | has_new |
| 设置页入口点击 | has_new |
| 实验室页面成功展示 | visible_count、enabled_count、retained_count、has_new |
| toggle submit | feature_key、from_enabled、target_enabled |
| toggle result | feature_key、target_enabled、result、error_type、duration_ms |

不要重复上报 SDK base schema 已有的 user/device/language 等字段，不上传 email 或完整策略 JSON。

平台流程：

1. 生成 `metric-spec.html` 和 `tracking-design.html`，完成一致性审查。
2. 查询目标 application 和已有事件，避免重复 point。
3. 按 analytics application 分别创建/复用事件和 schema；检查当前活跃工单版本，不默认 `1-0-0`，也不复制另一 App 的 schema version。
4. 若无适用工单，在 `https://us-analytics-management.theunismart.com/workOrder/list/` 创建/推进发布工单。
5. 完成本地 mock/test sink，再通过真实 App 操作验证 collector 和 schema。
6. 确认事件进入 good 数据和预期 DWD 后，再建只包含入口、页面到达、toggle 成功率和 P95 的 Superset 平台看板；不要在 DWD 未就绪时用空看板宣称完成。

## 7. 后端可观测性

结构化日志保留 `feature_key`、调用方、policy lifecycle、runtime result、deny/error reason、duration 和 trace ID，供单次功能测试与故障定位使用。用户 ID 只按公司日志规范放在可控字段，不作为 Prometheus label。

低基数指标固定为：

- NH list/summary/toggle 的请求量、错误率和延迟；
- NH→PE 的请求量、错误率和延迟；
- PE settings 读写、标准 EvalFeatures 和 Lab attributes Loader（`enabledLabFeatureKeys`、`disabledLabFeatureKeys`）的请求量、错误率和延迟。

不为 catalog、CMS 内容、单个 feature guard、IOT policy 或异步 generated/skipped 新增实验室专项指标。功能正确性由测试和现有日志/Trace 证明。

禁止把 user ID、device SN、trace ID、原始错误文本作为 metric label。

Grafana 只包含 NH list、toggle、NH→PE、PE settings、EvalFeatures 和 Loader 的 rate/error/p95。告警只覆盖 list 错误率、toggle 失败率、NH→PE 错误率和 PE core 聚合错误率。调用 `$prom-grafana-dev` 把 scrape/alert/dashboard 验证纳入 CI。

## 8. 发布和回滚

发布前记录：

- 每个 repo 的 base/head SHA、目标分支和 pipeline URL；
- 每个客户端的 source/target release、allowlist、tenant/bundle 和 analytics schema/work order；
- NineData task、CMS content version、Crowdin branch/version；
- GrowthBook 全环境 before/after；
- staging 测试证据和未完成手工项；
- dashboard/alert/埋点工单状态。

推荐回滚顺序：

1. 紧急情况下先把 policy 切到 `off,false,false`，删除或覆盖所有允许规则，确认 PE/IOT fail closed，停止成本副作用。
2. 再回滚客户端/服务端代码或 CMS 内容。
3. 不删除用户设置；保留 `enabled=false/true` 历史以支持审计和未来恢复。
4. 不通过 NH `status` 做紧急开关。
5. 回滚后用相同状态矩阵验证，并通过实际任务和现有日志/Trace 确认 off 时副作用为 0。

生产配置必须从已验证的 staging 语义提升，而不是初始化用户数据：

- 先读取 eligibility 与 policy 的完整 production `environments`，保存 before 证据。
- 默认 ON 使用可选 hard-deny、显式 OFF、显式 ON、末尾无条件 ON；默认 OFF 只改变末尾值。
- 只有确认覆盖所有 policy 消费者时才使用无条件默认 ON；受限人群必须复核 hard-deny。
- 默认 ON 仍为目标用户配置 eligibility，保证显式关闭后可以重新开启。
- API 写入完整 environments，复读并按业务字段做语义比较；忽略平台重建的 rule `id`/`definition`。
- 只修改已批准的 production 环境；确认 staging/pre 未发生业务字段变化。

## 9. 最终报告模板

```markdown
## 实验功能接入结果

| 项目 | 状态 | 证据/链接 | 阻断 |
|---|---|---|---|
| CMS/Crowdin | PASS/PENDING | ... | ... |
| NH catalog/PE/GB | PASS/PENDING | ... | ... |
| NH/IOT gates | PASS/PENDING | ... | ... |
| Client real entry | PASS/PENDING | ... | ... |
| Paid/non-paid | PASS/PENDING | ... | ... |
| Observability | PASS/PENDING | ... | ... |
| Staging E2E | PASS/PENDING | ... | ... |

### 需要用户手工验证
- 仅列必须依赖视觉判断、真实设备/事件或账号交互的项目。

### 上线阻断项
- 无 / 具体项、责任人、解除条件。
```
