# 仓库地图

> 数据来源：gitlab.addx.ai API（2026-05-07 快照），最近 30 天活跃。
> Refresh：跑 `glab api "projects?membership=true&order_by=last_activity_at&per_page=100"` 重拉。

## Namespace 分布

| Namespace | 仓数 | 类型 |
|-----------|-----|------|
| **DEV** | 27 | 基础设施 / DevOps / IaC |
| **CLOUD** | 17 | 云后端 / 微服务 |
| **DEVT** | 11 | 设备测试 / IoT 平台 |
| **SYSS** | 9 | 嵌入式 SDK / vendor SDK |
| **services** | 8 | 业务微服务（customer-care / notification 等）|
| **其他** | 13 | engineering / yluo / data / qa-workspace / frontend 等 |
| **SWCLIEN** | 5 | 移动客户端 |
| **FLUTTER** | 4 | Flutter 框架层（platforms/lattice 等） |
| **applications** | 2 | 独立应用（naturehood / golf） |
| **AUD** | 2 | 音视频 SDK |
| **IOS_MODULE** | 2 | iOS 模块 |
| **总计** | **100** | 分项求和 = 总计 ✅ |

> 数据口径：`glab api projects?membership=true` 返回 100 条 active 仓（2026-05-07 抓取）。
> 其中 27+17+11+9+8+5+4+2+2+2 = 87 是已明确分类的 namespace；剩余 13 是较小的 namespace（如 engineering / yluo / data / qa-workspace 等），统一归"其他"。

---

## 核心仓速查（按 scope）

### Lattice Scope（SPM: tiancailaxi 直接管 + 修复权）

| 仓 | 路径 | 角色 |
|----|----|----|
| flutter-lattice | `platforms/lattice` (本地 `lattice/lattice/`) | Flutter 模块化框架（52 包 + shell app + 6 工具） |
| g0-android | `SWCLIEN/g0-android` | Android 原生宿主（Kotlin） |
| g0-ios | `SWCLIEN/g0-ios` | iOS 原生宿主（Swift） |
| g0-flutter-module | `SWCLIEN/g0-flutter-module` | 旧 FlutterBoost 模块（参考用） |
| naturehood | `applications/naturehood` | 鸟类社区独立应用（Flutter packages + Go server + React hub + Vue admin） |
| smartdevicecoresdk-ios | `IOS_MODULE/smartdevicecoresdk-ios` | iOS 智能设备核心 SDK |
| cms_payment_plugin | `IOS_MODULE/cms_payment_plugin` | Flutter CMS 支付插件 |

### Lattice 可修但物理在 Gen3 Scope

| 仓 | 路径 | 角色 |
|----|----|----|
| iot-service-unified | `CLOUD/iot-service-unified` | IoT 核心后端（Java/Spring Boot 2.5.5 + gRPC 1.57.1）— library/timeline/VIP/bird filter |
| naturehood server | `applications/naturehood/server` | naturehood 业务后端（Go go-zero） |

### Gen3 Scope（lattice 集成无关时转交）

| 仓 | 路径 | 角色 |
|----|----|----|
| marketing-cms | `CLOUD/marketing-cms` | Payload CMS 后台（Next.js 15 + Payload 3.70） |
| marketing-service | `CLOUD/marketing-service` | 营销业务后端（Java + AB 实验） |
| cms-web-preview | `CLOUD/cms-web-preview` | Flutter Web 预览 |
| payment-core | `gen3/payment-core` | iOS 支付核心 SDK（Swift + Airwallex/Stripe） |
| flutter_payment | `lattice/flutter_payment` | CMS 支付旧版 Flutter |

### 其他业务仓（独立团队）

| 仓 | 角色 |
|----|----|
| `CLOUD/crm` + `CLOUD/crm-front` | CRM 系统（Java + Vue 2） |
| `CLOUD/revenue-sharing` + `CLOUD/revenue-sharing-front` | 收入分享系统 |
| `CLOUD/vip-service` | VIP 权益服务（Go） |
| `services/customer-care` | 客服工作台（TypeScript） |
| `services/notification` | 通知服务 |
| `applications/golf` | 高尔夫主题应用（C#） |
| `CLOUD/a4x-logger-sdk` | 统一日志 SDK（Java） |
| `AUD/app/safertc_live/a4xlivesdk` | 直播 SDK |

### 设备 / 嵌入式（DEVT + SYSS + AUD）

| 仓 | 角色 |
|----|----|
| `SYS/intelli_vision_fw` | 智能视觉摄像头固件（C） |
| `DEVT/device-cloud-host` | 设备云主控（Python） |
| `DEVT/device-cloud-server` | 设备云后端（Java） |
| `DEVT/device-cloud-frontend` | 设备云前端（TypeScript） |
| `DEVT/device-cloud-camera-plugins` `device-cloud-esp32-plugin` `device-cloud-relay-plugin` | 各类设备插件（Python） |
| `SYSS/internal/embed_core` | 嵌入式内核库（C） |
| `SYSS/vendor_sdk/vendor_sdk_hc32l110x` | HC32L110 芯片 SDK（C） |

### 基础设施（DEV）

| 仓 | 角色 |
|----|----|
| `DEV/argocd-apps` | ArgoCD 应用配置 |
| `DEV/IaC` | Terraform 基础设施代码（HCL） |
| `DEV/k8s` | K8s 配置脚本 |
| `DEV/feishu-bitbucket-integration` | 飞书集成（Python） |

### 数据 / BI

| 仓 | 角色 |
|----|----|
| `data/tracker-management` | 埋点管理（Spring Boot） |
| `data/tracker_manager_frontend` | 埋点管理前端（React） |
| `data/bi` | BI 分析（Streamlit + Pandas + Dagster） |
| `data/growthbook-cli` | GrowthBook CLI |
| `data/lowcode-engine` | 低代码编辑引擎 |

### QA

| 仓 | 角色 |
|----|----|
| `qa-workspace/qa-tools` | QA 自动化平台（React + Playwright + Better-SQLite3） |

### 工程基础

| 仓 | 角色 |
|----|----|
| `engineering/skills` | 工程 skills 注册中心 |
| `frontend/web` | 微前端主应用（pnpm + qiankun + React 18） |
| `frontend/micro_app_platform` | 微应用管理后端（Spring Boot） |
| `yluo/aide` | 开发辅助工具 |

---

## 仓库快速定位（按业务问题反查）

| 问题域 | 优先看哪个仓 |
|--------|------------|
| Library / 视频回看 / 鸟类识别 | `iot-service-unified` `g0-android` `g0-ios` `lattice` |
| 支付 / Paywall / 订阅 | `cms_payment_plugin` `flutter_payment` `payment-core` `marketing-service` `marketing-cms` |
| Timeline / Story | `naturehood/server` `naturehood/packages` `iot-service-unified` |
| 推送 / 通知 | `services/notification` |
| 设备绑定 / 配网 | `g0-android` `g0-ios` `iot-service-unified` `intelli_vision_fw` |
| AB 实验 / 灰度 | `marketing-service` `data/growthbook-cli` |
| 埋点 / 数据 | `data/tracker-management` `data/bi` |
| K8s 部署 | `DEV/argocd-apps` `DEV/k8s` |
| 国际化 / Crowdin | `lattice` 内 `tools/crowdin_sync` |

---

## 完整 100 仓索引

详细列表请运行（任选一种，按本地工具可用性选）：

```bash
# 方案 A：用 jq（如已安装）
glab api "projects?membership=true&order_by=last_activity_at&per_page=100&simple=true" \
  | jq -r '.[] | "\(.path_with_namespace)\t\(.last_activity_at)"' \
  | sort -k2 -r
```

```bash
# 方案 B：用 python（无 jq 时）
glab api "projects?membership=true&order_by=last_activity_at&per_page=100&simple=true" \
  | python3 -c 'import json,sys; [print(f"{p[\"path_with_namespace\"]}\t{p[\"last_activity_at\"]}") for p in json.load(sys.stdin)]' \
  | sort -k2 -r
```

```bash
# 方案 C：用 node（CI 标准容器内通常可用）
glab api "projects?membership=true&order_by=last_activity_at&per_page=100&simple=true" \
  | node -e 'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{JSON.parse(d).forEach(p=>console.log(p.path_with_namespace+"\t"+p.last_activity_at))})' \
  | sort -k2 -r
```

```bash
# 方案 D：直接用 glab 输出（不解析 JSON）
glab api "projects?membership=true&order_by=last_activity_at&per_page=100"
```

或直接看 GitLab Web：https://gitlab.addx.ai/explore/projects?sort=latest_activity_desc
