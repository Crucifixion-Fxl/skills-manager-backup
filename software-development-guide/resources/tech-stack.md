# 技术栈速查

> 数据来源：本地仓 `pubspec.yaml` / `build.gradle` / `pom.xml` / `package.json` / `Podfile`（2026-05 实读）
> Refresh：每月或大版本升级时手动 re-scan 各仓配置文件

---

## 全栈期望（重要 — 不是只看自己那一栈）

公司开发规则：**产品 dev 全栈，归属 1-2 条业务主线深耕，主线内跨栈**。

| 角色 | 栈期望 |
|------|--------|
| 产品 dev | 客户端 (Android + iOS + Flutter) + 后端 (Java/Go) + 前端 (React) + 数据埋点 全部能上手；归属主线内深度精通 |
| QA | 不强制业务全栈，但需"测试左移"：协助开发写 unit/integration test，QA 兜底 E2E + 探索 |
| SRE / DevOps | 不强制业务全栈，但所有人都必须有 CI/CD 能力 |
| 数据 / BI | 不强制业务全栈，但所有人都必须有数据驱动思维 + dbt/dagster/superset 工具链 |
| 算法 | 同数据 + AI 工程能力 |

**全员（含独立团队）共通技能**：
- CI/CD 能力（→ `gitlab-ci` `argocd` `argocd-deploy` `cicd-developer` skill）
- 数据驱动思维（→ `data-driven-investigation` `tracking-lifecycle` `growthbook` `superset` skill）
- AI 工程能力（→ `claude-api` `agentic-engineering` `ai-first-engineering` skill）
- 测试基础（→ `tdd-workflow` `testing-strategy` skill）

新人第一周不必每条栈都精通，但要"知道 X 是什么 + 在哪个仓 + 出问题去找哪个 skill"。

---

## 客户端栈

### Flutter Lattice（核心客户端框架）

| 项 | 值 |
|----|----|
| 语言 | Dart 3.5.x |
| 框架 | Flutter 3.5.x |
| 仓 | `platforms/lattice`（本地 `lattice/lattice/`）|
| 结构 | Mono-repo（52 packages + 1 shell app + 6 tools）|
| 包管理 | melos 6.3.0 |
| DI | LatticeDI（自研，详见 `lattice-di` skill）|
| 状态管理 | provider（lattice_state 包封装） |
| 路由 | LatticeModule.routes（自研模块化路由） |
| 测试 | dart test / flutter test / monkey test (integration) |
| 质量门禁 | dep_checker（9 条架构规则）+ dart analyze + dart format + quality_gate |
| 子 skill | `lattice-architecture` `lattice-testing` `lattice-debugging` `lattice-di` `lattice-capability` `lattice-native-bridge` `lattice-quality-gate` `dart-flutter-patterns` |

### Android（g0-android）

| 项 | 值 |
|----|----|
| 语言 | Kotlin |
| 构建 | Android Gradle 7.x |
| 架构 | Clean Architecture + MVI |
| DI | Hilt |
| UI | XML + ViewBinding（不用 Compose）|
| 关键依赖 | GreenDAO 3.3.0、Bugsnag 7.x、Kace 1.9.20、Google Services 4.4.2、Sentry、FlutterBoost |
| 子 skill | `kotlin-patterns` `kotlin-build` `android-dev-setup` `android-clean-architecture` `start-feature-development` `fix-critical-bug` `optimize-performance` `review-code` |

### iOS（g0-ios）

| 项 | 值 |
|----|----|
| 语言 | Swift |
| 构建 | CocoaPods（含自研 hooks/smart-pod）|
| 最低系统 | iOS 12.0 |
| 关键依赖 | Smart Device Core SDK、Flutter Module、Airwallex、Stripe |
| 双端一致性 | 与 Android 严格对齐：ABTest key、埋点事件、频控、支付/推送流程 |
| 子 skill | `swiftui-patterns` `swift-concurrency-6-2` `swift-actor-persistence` `swift-protocol-di-testing` |

---

## 后端栈

### Java / Spring Boot（主力）

| 项 | 主流值 |
|----|--------|
| Java | 11（少数仓 8）|
| Spring Boot | 2.5.5（一致）|
| Spring Cloud | 2020.0.4 |
| 微服务通信 | gRPC 1.57.1 + Protobuf 4.29.1 |
| ORM | MyBatis 3.4.6 / MyBatis-Plus 3.5.2 |
| 数据库 | MySQL 8.0.13 |
| 缓存 | Redis + Caffeine |
| 日志 | a4x-logger-sdk（统一结构化日志） |
| 云 SDK | AWS / OCI / GCP |
| 涉及仓 | iot-service-unified、marketing-service、crm、revenue-sharing、tracker-management、micro_app_platform |
| 子 skill | `springboot-patterns` `springboot-security` `springboot-tdd` `springboot-verification` `jpa-patterns` `microservice-integrate` `a4x-logger-onboarding` |

### Go

| 项 | 值 |
|----|----|
| 框架 | go-zero |
| 数据库迁移 | golang-migrate（嵌入二进制） |
| 认证 | JWT HS512（生产）/ X-User-Id header（APISIX 注入）/ Mock（CI） |
| 涉及仓 | naturehood/server、vip-service |
| 子 skill | `golang-patterns` `golang-testing` |

### Node.js / Next.js（CMS）

| 项 | 值 |
|----|----|
| 框架 | Next.js 15.4.10 |
| 语言 | TypeScript |
| CMS | Payload CMS 3.70.0 |
| 数据库 | MongoDB (@payloadcms) |
| 存储 | AWS S3 |
| 测试 | Playwright |
| 涉及仓 | marketing-cms |

### TypeScript / NestJS

| 项 | 值 |
|----|----|
| 框架 | NestJS（部分微服务） |
| 涉及仓 | services/customer-care |
| 子 skill | `nestjs-patterns` |

### Python

| 项 | 值 |
|----|----|
| 用途 | 数据 BI / 设备插件 / 工具 |
| 关键库 | Streamlit、Pandas、Plotly、Dagster、PyAthena |
| 涉及仓 | data/bi、DEVT/device-cloud-camera-plugins、engineering/skills |
| 子 skill | `python-patterns` `python-testing` |

### C / C++（嵌入式）

| 项 | 值 |
|----|----|
| 用途 | 设备固件（intelli_vision_fw）+ vendor SDK |
| 涉及仓 | SYS/intelli_vision_fw、SYSS/* |
| 子 skill | `cpp-coding-standards` `cpp-build` `cpp-testing` |

---

## 前端栈

### React（主流）

| 项 | 值 |
|----|----|
| React | 18.2 / 19.2（marketing-cms） |
| 微前端 | qiankun（frontend/web） |
| UI 库 | Ant Design Pro |
| 构建 | Vite / Webpack |
| 包管理 | pnpm（mono-repo） |
| 涉及仓 | frontend/web、qa-tools、marketing-cms、tracker_manager_frontend |

### Vue 2（老栈，逐步迁移）

| 项 | 值 |
|----|----|
| Vue | 2.6.10 |
| UI 库 | Element-UI 2.11.0 |
| 工具 | Vue CLI 3.9.0 + Axios + Moment |
| 涉及仓 | crm-front、revenue-sharing-front |

---

## 数据 / BI

| 项 | 值 |
|----|----|
| 数据编排 | Dagster（含 dbt 模型） |
| 可视化 | Apache Superset + Streamlit |
| 数据仓 | ClickHouse + Athena |
| 元数据 | DataHub |
| 埋点 | 自研 tracker-management（Spring Boot + React 前端） |
| AB 实验 | GrowthBook |
| 子 skill | `superset` `dagster` `dagster-dbt-model-create` `dagster-dbt-model-execute` `datahub` `tracking-lifecycle` `growthbook` |

---

## DevOps / 基础设施

| 项 | 值 |
|----|----|
| 容器编排 | Kubernetes（EKS / TKE / GKE 多云）|
| GitOps | ArgoCD |
| CI/CD | Jenkins + GitLab CI |
| IaC | Terraform |
| 远程缓存 | BuildBuddy（Bazel）|
| 云 CLI | AWS / GCP / Azure / Aliyun / Tencent / UCloud / OCI 全栈 |
| 监控 | Prometheus + Grafana |
| 日志 | FluentBit → Kafka → Vector → Elasticsearch |
| APM | Sentry + Bugsnag |
| 跨平台权限 | Casdoor（飞书 SSO）|
| 密钥管理 | HashiCorp Vault |
| 子 skill | `argocd` `argocd-deploy` `jenkins` `gitlab-ci` `k8s-ops` `vault-kv-manager` `aws-cli` `gcp-cli` `prometheus` `grafana` `log-ingestion-elasticsearch` `sentry-onboarding` |

---

## 一致性规范

- **Spring Boot 2.5.5 / gRPC 1.57.1 / Protobuf 4.29.1** 在所有 Java 后端服务统一
- **Java 11** 是主流（marketing-service / tracker-management 等老仓还是 Java 8，待升级）
- **Vue 2 → React** 前端整体迁移中，老仓 (crm-front / revenue-sharing-front) 短期不动
- **g0-flutter-module → lattice** Flutter 模块化框架替换中
- **Crowdin** 是唯一多语言来源，**单向覆盖**（本地未注册的 key 会被同步删掉）
- **Casdoor + 飞书 SSO** 是统一登录入口

---

## 全栈跨栈学习路径（推荐）

新人按"主线归属 → 栈拓展"顺序学：

1. **第一栈**（深度，1-2 周）：你主线最常用的栈 — 跑通环境 + 改一个 issue + 起 MR
2. **第二栈**（基础动手，2-4 周）：本主线另一栈 — 比如 IoT 主线先 Android 再 iOS / 再 iot-service Java
3. **第三栈**（基础动手，1-2 个月）：本主线再扩 — 如 Flutter / Go
4. **共通能力**（持续）：CI/CD + 数据驱动 + 测试 + AI 工程
5. **第二条主线**（半年后）：通过跨主线 issue 接触 — 增加视野

**禁止**：第一周就同时学 5 个栈 → 全栈 ≠ 同时全栈，是"按需 + 累积"。
