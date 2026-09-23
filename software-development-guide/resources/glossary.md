# 业务术语 + 缩写表

> 来源：lattice/.aide 活跃变更 + 各仓 CLAUDE.md + 实地代码检索。
> 用法：新人在群里 / 文档里看到不懂的缩写，先查这里。

---

## 产品 / 业务

| 缩写 / 术语 | 含义 |
|------------|------|
| **kb** | 公司核心品牌之一缩写，喂鸟器 + 摄像头智能设备（外部场合统一用 kb 代称，避免直接使用品牌名）|
| **vh / vico home** | 另一品牌缩写，安防摄像头方向，与 kb 共用同一套 SDK |
| **vn / VicoNature** | Vico 家族的鸟类社区方向品牌 |
| **golf** | 高尔夫主题应用（applications/golf） |
| **Naturehood** | 鸟类社区平台（applications/naturehood）— 鸟类图鉴 + Story / PostCard 社交 + Ecosystem 探索 |
| **PostCard** | naturehood 内的"社交分享卡片"，分享鸟类故事 |
| **Ecosystem** | naturehood 内的"地理生态"探索 tab |
| **Collection** | naturehood 内的"我的鸟类收藏" |
| **Explore Tab** | naturehood 内的"探索" tab |
| **KBTV** | KB 品牌的 TV 功能 — 类似抖音视频流的鸟类视频刷流（不是"Knowledge Base TV"）|
| **Library** | 客户端"视频回看"页面（旧版叫 Gallery） |
| **Timeline / Story Feed** | 用户拍到的鸟的时间线 / 故事流 |
| **Family Premium** | VIP 家庭分享套餐 |

## 设备 / IoT

| 缩写 / 术语 | 含义 |
|------------|------|
| **PIR** | 人体红外（Passive Infrared）触发的视频事件 |
| **FVP** | Flutter Video Player 视频包（lattice 内 fvp 包） |
| **NewEvent** | iot-service `/library/newselectlibrary/newevent` 接口，timeline / library 数据来源 |
| **eventCount** | NewEvent 接口返回的事件条数（监控指标） |
| **bird filter** | iot-service 内对 timeline 按"鸟类"过滤的逻辑 |
| **setting-override** | iot-service 设备设置覆盖机制（bind 时下发） |
| **device sn / serialNumber** | 设备序列号（去重 / 关联用） |
| **bundle** | 客户端 app bundle id（kb 端为 `com.kb.<brand>`，nature 端为 `com.naturehood.app` 等；具体值在各仓 build.gradle / Podfile） |
| **tenantId** | 多品牌租户标识（kb / vh / naturehood）|
| **buildEnv** | staging / prod-k8s / pre / test |

## 支付 / VIP

| 缩写 / 术语 | 含义 |
|------------|------|
| **Paywall** | 付费墙页面（CMS 配置） |
| **Tier** | VIP 等级 |
| **SKU** | 商品款式 / 套餐 ID |
| **IAP** | In-App Purchase（苹果/谷歌内购） |
| **Stripe / Airwallex** | 海外支付通道 |
| **ratio_backend_pay_failed** | 后端支付失败率（SLA 告警指标）|
| **ratio_bind_pay_card** | 绑卡支付占比（指标） |
| **card_pay_success_ratio** | 卡支付成功率（指标） |
| **bird_detection_rate** | 鸟类识别率（核心业务指标） |

## 客户端架构 / Lattice

| 缩写 / 术语 | 含义 |
|------------|------|
| **Lattice** | Flutter 模块化框架（platforms/lattice），逐步替换旧 g0-flutter-module |
| **\*_api / \*_feature** | Lattice 包命名规范：`*_api` 只放接口/抽象，`*_feature` 是实现 |
| **shell_app** | Lattice 的应用壳（apps/shell_app），bootstrap 模块注册 |
| **dep_checker** | Lattice 自研架构规则检查器（9 条规则强制） |
| **quality_gate / dev_gate** | Lattice 综合质量门禁（test / secret / coverage / user_story） |
| **Split API / EventBus / Shared Widget** | Lattice 跨模块通信三种 DI 模式 |
| **MethodChannel / Native Bridge** | Flutter ↔ 原生宿主的通信通道（lattice ↔ g0-android/ios） |
| **bridge_protocol.yaml** | Native Bridge 协议定义（自动 codegen） |
| **LatticeModule** | 每个 feature 包必须实现的接口（metadata / dependencies / routes / lifecycle） |
| **module_registry.yaml** | shell_app 内的模块注册清单 |

## 后端架构 / iot-service

| 缩写 / 术语 | 含义 |
|------------|------|
| **DDD 分层** | domain-interface / domain-service / site-controller |
| **gRPC** | 微服务间通信（gRPC 1.57.1 + Protobuf 4.29.1） |
| **Sharding JDBC** | iot-service 用的分库分表中间件（ShardingSqlProvider） |
| **APISIX** | API 网关（注入 X-User-Id header） |
| **a4x-logger-sdk** | 统一结构化日志 SDK |
| **iot-service-cloud / iot-service-local** | 云端 / 本地两套部署，同 codebase |

## 测试 / 工具

| 缩写 / 术语 | 含义 |
|------------|------|
| **TDD** | 测试驱动开发（先写测试再实现）|
| **Monkey test** | Lattice 集成测试（自动随机点击）|
| **E2E** | 端到端测试（Playwright / Flutter integration_test）|
| **L1 / L2 / L3 / L4** | 测试金字塔分层（单元 / 集成 / E2E / 探索）|
| **dep_checker** | Lattice 架构检查 |
| **qa-tools** | 公司 QA 自动化平台（造数 / E2E / 测试账号）|
| **mock-engine** | 本地 Mock 服务（造接口数据）|

## 数据 / 监控

| 缩写 / 术语 | 含义 |
|------------|------|
| **Snowplow** | 埋点 SDK（客户端 + 后端）|
| **GrowthBook** | AB 实验平台 |
| **DataHub** | 数据资产元数据平台 |
| **Dagster** | 数据编排平台 |
| **dbt** | 数据建模工具（在 Dagster 内）|
| **ClickHouse / Athena** | 数仓 |
| **Superset** | BI 可视化 |
| **SLA 告警** | 业务指标级告警（dapp.addx.live 配置）|
| **trouble_shooting_id** | troubleshooting 平台脱敏后的查询 ID |

## DevOps / 平台

| 缩写 / 术语 | 含义 |
|------------|------|
| **Casdoor** | 公司统一 SSO（飞书）|
| **Vault** | HashiCorp 密钥管理 |
| **ArgoCD** | K8s GitOps 部署 |
| **Kyverno** | K8s 策略引擎 |
| **NineData** | 数据库 DevOps 平台（SQL 审批 / 审计）|
| **Apollo** | 配置中心 |
| **Crossplane** | K8s Provider（外部资源管理）|
| **BuildBuddy** | Bazel 远程缓存 |
| **dapp / dapp-api** | dapp.addx.live（Streamlit 前端）+ dapp-api.addx.live（Bearer auth API），SLA 指标平台 |
| **Mock Engine** | 本地 Mock 服务环境 |

## 协作 / 流程

| 缩写 / 术语 | 含义 |
|------------|------|
| **SPM** | Software Project Manager（软件项目经理 / 主窗格管理者）|
| **Worker** | SPM 派发的子 agent（在 cmux pane 内执行任务）|
| **RFC** | Request For Comments（方案设计文档）|
| **PRD** | Product Requirements Document |
| **US** | User Story |
| **MR** | Merge Request（GitLab 用）|
| **Crowdin** | 多语言翻译平台（单向覆盖）|

## 性能 / 数据指标

| 缩写 / 术语 | 含义 |
|------------|------|
| **P50 / P95 / P99 / P99.9** | 百分位延迟。例如"接口 P95=300ms" = 95% 请求 ≤ 300ms 返回，5% 慢于 300ms。P99/P99.9 用于尾部延迟监控（用户体验长尾）|
| **QPS / TPS** | Queries Per Second / Transactions Per Second — 每秒查询数 / 事务数 |
| **RPS** | Requests Per Second — 每秒请求数 |
| **Latency / RT** | 延迟 / 响应时间（Response Time）|
| **Throughput** | 吞吐量（单位时间处理量）|
| **SLA** | Service Level Agreement — 服务等级协议（外部承诺的可用性 / 性能上限）|
| **SLO** | Service Level Objective — 服务等级目标（内部要达成的指标，比 SLA 严）|
| **SLI** | Service Level Indicator — 实测指标（如可用性 99.95%、P95 200ms）|
| **MTTR / MTTD** | Mean Time To Recovery / Detection — 平均恢复 / 发现时间 |
| **Error Budget** | 错误预算 = (1 - SLO)。如 SLO=99.9% → 月度可用宕机预算 ≈ 43 分钟 |
| **DAU / MAU / WAU** | 日 / 月 / 周活跃用户数 |
| **Retention** | 留存率（次日 D1 / 7 日 D7 / 30 日 D30）|
| **Churn** | 流失率（订阅类常用：月度退订率）|
| **CTR** | Click Through Rate — 点击率 |
| **CVR** | Conversion Rate — 转化率（如付费转化 / 注册转化）|
| **ARPU / ARPPU** | Average Revenue Per User / Per Paying User |
| **LTV** | Lifetime Value — 用户生命周期价值 |
| **Funnel** | 漏斗指标（多步流程的逐步转化率，如"注册→绑定→支付"）|
| **eventCount** | 业务指标：iot-service NewEvent 接口返回的事件数（监控 timeline 接口异常用）|
| **bird_detection_rate** | 业务指标：鸟类识别率（KB 核心业务）|
| **CWV** | Core Web Vitals — Web 性能三大指标（LCP / INP / CLS）|

## 其他常见名字

| 名字 | 是什么 |
|------|------|
| **iot-service / iot-service-unified** | 同名旧/新仓，新版用 unified |
| **gen3** | 老一代后端工作区路径（含 iot-service / naturehood/server / marketing-* 等）|
| **lattice scope** | tiancailaxi 主导的客户端 + 关联后端范围 |
| **g0-android / g0-ios** | "g0" 是公司客户端代号（Generation 0？）|
| **smartdevicecoresdk-ios** | iOS 智能设备核心 SDK（client SDK 层）|
| **a4x** | 公司技术品牌缩写（出现在多个 SDK 名 a4x-logger / a4xlivesdk）|
| **A4X / SafeRTC** | 直播 / RTC SDK |
