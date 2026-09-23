# 平台录入与代码改动总表

## 目录

- 接入场景和平台录入矩阵
- CMS、Crowdin 操作
- NH、IOT、Lattice、Native 代码触点
- Nexus package 发布与禁止事项

## 1. 先判断是哪一种接入

| 场景 | 数据平台动作 | 代码动作 |
|---|---|---|
| 新功能接入已支持实验室的 App | 新增 NH 目录行、CMS 内容、Crowdin 文案、GrowthBook eligibility + policy | 增加真实能力门禁、客户端 key/allowlist/resolver/入口；按能力归属决定是否改 NH 或 IOT |
| 已有功能接入新 App | 通常不新增 NH/PE 数据；先扩展并验证 GB tenant/bundle/version eligibility 与 policy，再补该 App 的 Crowdin/CMS 品牌内容 | 首次接入时补 Native 设置入口和 Lattice module bootstrap；增加客户端 allowlist、真实入口与品牌差异；发布 package 后让目标 release 显式消费 |
| 发布前撤回某 App 的已有功能接入 | 不为从未属于该 App 的能力新增/修改 GB；独立后端入口如需按 App 禁止，使用产品 capability guard | 从该 App allowlist 和真实入口移除；关闭未合并 MR；发布新 package 并让目标 release 消费；回归 list/update/深链 fail closed |
| 新环境首次部署实验室平台 | 建好 PE database/user、`user_feature_settings`，运行 NH migration，配置 CMS/Crowdin/GB 环境 | 部署 PE、NH、客户端和必要的能力服务 |
| 只调整实验生命周期、默认状态或灰度 | 只修改 GrowthBook 完整 environment 配置 | 通常无需发版；仍需回归 list、toggle 和真实能力 |

## 2. 必须录入的平台

| 平台 | 录入内容 | 新功能是否必需 | 执行方式与关键约束 |
|---|---|---:|---|
| Naturehood MySQL | `lab_feature` 一行目录数据 | 是 | 表结构由 NH `golang-migrate` 创建；调用 `$ninedata` 提交 DML SQL 任务插入目录行，审批/执行后回查；禁止直连执行，不把 seed 写进 migration |
| PE MySQL | `personalization_engine.user_feature_settings` | 否，环境首次建设才需要 | database/user Ready 后走 NineData DDL；不插 seed；新功能和新用户都不初始化行 |
| GrowthBook | 一个 boolean eligibility feature + 一个 JSON runtime policy | 是 | eligibility 控制版本、tenant、会员展示和灰度；policy key 为 `lab-<kebab-feature-key>-policy`；policy 核心顺序为显式 OFF、显式 ON、环境默认，运行时 hard-deny 在其之前；实际能力只认最终 `runtimeEnabled` |
| Marketing CMS | `lab-feature-content` 记录及 icon/iconDark/image | 是，除非经评审使用品牌本地资产 | 只在 staging 编辑并 Publish；再 staging -> pre、pre -> prod Transfer；Publish 不等于跨环境同步 |
| Crowdin | 功能 name/description；首次接入 App 时还包括实验室入口和通用页面文案 | 是 | 每个会消费该文案的 App 项目都要录入；KB 与 Vico Nature 当前分别核对 KB、`app_vn` 项目；补齐全部目标语言后用仓库脚本拉取，禁止手改生成文件 |
| Analytics Management | 实验室入口、页面、toggle 的通用事件 schema/work order | 每个新 App 首次接入时必需；单个新功能通常复用 | 不为每个 feature 新建业务事件；没有可承载 schema 的工单时才新建工单 |
| Grafana/Prometheus | NH/PE 实验室平台聚合 dashboard/alert | 平台首次接入时必需；单个新功能通常复用 | 不为 CMS 或每个实验功能单独建实验室指标 |

GrowthBook 和 CMS 是配置平台，不要把它们的变更伪装成数据库 seed。NineData 只执行已经确认归属的 DDL/DML，不负责创建 GrowthBook、Crowdin 或 CMS 内容。

环境默认 ON/OFF 不通过初始化 `user_feature_settings` 实现。无用户节点时两组 PE attributes 都为空，由 policy 末尾的环境默认规则决定；用户首次切换到不同状态时才懒写显式 true/false。无条件默认 ON 会让 eligibility=false 的用户也得到 runtime true；仅当目标是所有受支持用户时使用，否则先配置 policy hard-deny。默认 ON 仍需让目标用户命中 eligibility，否则用户关闭后不能重新开启。

已有功能扩展到新 App 时，不能只改客户端 allowlist。发布前必须让目标 App 的 tenant/bundle/version 命中 eligibility，并跑通“无显式节点 -> 默认状态、显式 OFF、再次 ON”矩阵。典型错误信号是：功能初始或历史状态为 ON，关闭后从实验室消失。此时先检查 PE 评估是否为 `gbEligible=false`、policy 是否从 `runtimeEnabled=true` 变为 `false`；NH active 可见性为 `platformVersionMatched && (gbEligible || runtimeEnabled)`，该状态会按设计隐藏，和客户端首次加载缓存无关。

反之，产品确认某能力从未属于某 App 时，不要为了表达产品矩阵而新建 GB hard-deny。客户端 allowlist 必须同时约束 list、summary、update、真实入口和 deep link；这组 capability gate 是该 App 的交付边界。只有后端入口可以绕过客户端独立执行、且 App 归属本身属于业务授权条件时，才增加独立于实验 policy 的产品 capability guard。

## 3. CMS 发布顺序

`lab-feature-content` 已注册 Transfer/Sync 策略。标准顺序为：

1. 在 `https://marketing-cms-staging-us.addx.live` 创建或更新并 Publish。
2. 调 staging 的 `POST /api/transfer/lab-feature-content/<key>` 同步到 pre。
3. 在 `https://marketing-cms-pre-us.addx.live` 通过 App-facing API 验证 key 和媒体 URL。
4. 调 pre 的同一路径同步到 prod。
5. 查询 `sync-logs`，并从目标环境验证 `GET /api/v1/lab-feature-content/<key>?locale=en`。

Admin 页面只看到 Publish 时，不代表没有 Transfer 能力；使用 `$marketing-cms` 的 API 流程，不要把重复 Publish 当作跨环境同步。Transfer 不可跳过 pre，也不能只验证后台预览。

## 4. Crowdin 两层文案

### 4.1 功能目录文案

每个功能固定创建：

```text
lab_feature.<feature_key>.name
lab_feature.<feature_key>.description
```

这些 key 写入 NH `crowdin_keys`，并在每个目标 App 的 Crowdin 项目中存在。当前 KB/VN 双端发布时，至少同时核对 KB 与 `app_vn`；不要因为 KB 已有就假设 VN 能拉到。

### 4.2 实验室通用文案

`lab_features`、空态、加载失败、toggle 错误等属于客户端通用文案。一个 App 首次接入实验室时，将这些 key 录入该 App 项目；之后新增单个 feature 不重复创建。

### 4.3 拉取规则

1. 先读取目标 release 分支中的 Crowdin config、bundle 和语言清单。
2. 用 `$crowdin` 完成源字符串和全部目标语言。
3. 使用仓库现有脚本拉取。Lattice 文案使用 `lattice/tools/crowdin_sync` 及其 `crowdin_sync.yaml`；Android/iOS 使用各仓 `crowdin/l10n.py` 和目标 app config。
4. 运行 l10n generator；生成的 ARB/Dart/XML/strings 只能由脚本产出。
5. 比较所有目标语言 key 集合，不能只凭 pipeline 通过认定翻译完整。

## 5. 新功能代码触点

### 5.1 Naturehood server

- 如果 NH 直接使用功能，增加 `FeatureKey...` 常量。
- 单个能力边界调用 `experimentalfeatures.CheckFeatureAllowed`。
- timeline 或一个请求涉及多个功能时调用 `experimentalfeatures.CheckFeaturesAllowed`，只评估一次。
- mutation 在查询/写入前拒绝；query/projector 在禁用时不查询 enrichment 并清除历史受控字段。
- 所有旧 route、批量接口、纠错/删除接口和 deep-link 对应 API 都要纳入调用面。

### 5.2 IOT 或其他异步执行服务

- 仅在该服务承担真实能力或成本副作用时修改。
- 保留原设备、权益、地区和业务 eligibility，再调用通用 `LabFeatureRuntimeGate.isEnabledForUser(userId, featureKey)`。
- 在 enqueue、AI task flag、模型调用或结果写入前 fail closed。
- IOT 调 PE 的标准评估接口，不调用 NH、不读 NH 表、不直读 PE 原始设置。

### 5.3 Lattice/Naturehood client

- 在 `lab_feature_api` 的 `LabFeatureKeys` 增加 key。
- 在 `LabFeatureClientConfig` 的目标 App allowlist 中显式加入；不支持的 App 不加入。
- 在 `LabFeatureStrings.featureTitles/featureDescriptions` 增加 name/description 映射；未知 key 不回退为其他功能。
- 真实业务入口在渲染和点击/路由两层调用 `LabFeatureStore.canUse(featureKey)`。
- 页面订阅 `LabFeatureStore.changes`，toggle 后刷新受影响的数据。
- 若品牌 icon 不同，使用 `LabFeatureClientConfig.prefersBrandIcon` 或同等级的 `client + feature_key` 精确覆盖。

实验室页面必须作为完整 Lattice module 交付：

```text
Native: lattice://lab-features
  -> G0 通用 Lattice/FlutterBoost adapter
  -> ModuleRegistry.resolveRoute('/lab-features')
  -> LabFeatureModule
       routes + Service/Store DI + NoticeCoordinator
       + NativeBridge subscriptions + dispose/lifecycle
```

不要把新功能的路由、DI、通知监听或生命周期重新散落到 G0 Flutter。G0 只注入 `LabFeatureClientConfig` 和通用 `LabFeatureNoticeBridge`。`flutter_lab_features_page` 只是兼容别名，新增代码统一使用 `lattice://lab-features`。

### 5.4 Android/iOS 宿主

- 已接入实验室的 App 新增单个 feature 时，通常无需改 Native 设置入口。
- 新 App 首次接入时，共享 `isNatureApp`/`isNature` 宿主判断，注册设置入口、红点 bridge、入口埋点和 `lattice://lab-features` 路由。
- 不再增加 tenantId 的重复 allowlist 判断；具体 feature 支持范围由 `LabFeatureClientConfig.allowedFeatureKeys` 管理。
- 若真实能力是 Native 页面，仍需在该 Native 入口和 deep link callback 消费实验状态；红点不是授权。

## 6. Nexus package 发布

Lab Feature 页面和 module 位于 Naturehood/Lattice package，不把业务实现复制进 G0 Flutter。改动 package 后：

1. 按 Lattice package lifecycle 计算下一个单调递增版本，并发布受影响 package 到 Nexus。
2. 相互依赖的 `lab_feature_api`、`lab_feature_feature`、`timeline_feature` 等使用同一批可解析版本。
3. G0 Flutter `pubspec.yaml` 只引用 Nexus hosted 版本；禁止提交本地 path/git 临时依赖。
4. 更新 lockfile 后确认 Android/iOS 构建实际嵌入新的 Flutter commit 和 package version。
5. 从目标 App 的 release 分支读取 `pubspec.yaml` 和 `pubspec.lock`，再下载对应 Nexus archive 核对 allowlist/入口实现；Naturehood package MR 合并或发布成功不代表目标客户端已经消费。

“比 release 当前版本加 1”只适用于仓库当前采用的 prerelease 序列；必须读取 CI/package lifecycle，不在 SOP 中硬编码某个历史版本号。

## 7. 不需要做的事

- 不为新功能给所有用户预插 PE 记录。
- 不为单个新功能重新建 `user_feature_settings` 表或 schema source。
- 不用 `lab_feature.status` 上下线功能。
- 不让 IOT 依赖 NH。
- 不在 CMS 存标题/简介翻译。
- 不在 Android、iOS、G0 Flutter 各复制一套 Lab Feature 业务逻辑。
- 不为每个 feature 新建一套实验室 metrics/dashboard。
