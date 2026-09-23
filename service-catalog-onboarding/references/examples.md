# 真实样例 — 平台型 / 垂直产品 / SDK 仓 / Flutter App / 嵌入式

> SKILL.md §1 提到的「2-3 个参考样例」详细版。看这个 reference 当：刚开始建 catalog 拓扑、不确定切几个 System / Component / 哪个 type、想找类似形态的 owning repo 参照。

## 实例 A — 平台型产品（增值业务，跨多个产品线复用）

```
Domain: monetization (变现父域)                    ← engineering/architecture/catalog/domains.yaml（无 owning repo）
├─ Domain: engagement  subdomainOf: monetization   ← services/value-added/engagement 仓根（有 owning repo）
│   ├─ System: engagement-backend  · Component: engagement-service(service,Go) + marketing-service(service,Java,迁出中)
│   ├─ System: engagement-console  · Component: engagement-admin(website,Next.js)
│   └─ System: engagement-sdk      · Component: engagement-flutter-sdk(lib) + engagement-ios-sdk(lib) + cms-payment-plugin(lib)
├─ Domain: vip  subdomainOf: monetization          ← CLOUD/vip-service 仓根（有 owning repo）
│   ├─ System: subscription-billing / vip-console / entitlement / vip-sdk · Component: vip-service / premium-management-* / vip-entitlement / flutter-payment-sdk / pay-core
└─ (将来) Domain: ads  subdomainOf: monetization
```

读法：`monetization` **无 owning repo**（跨多仓），住 architecture 仓；`engagement` / `vip` **有 owning monorepo**，分别住自己 monorepo 根。`engagement-sdk` 三个 SDK Component 在一个 System ——「端上接入增值导购」是同一件业务事的不同实现，不按端拆。

平台型产品的特征：被多个垂直产品消费、SDK / Backend / Admin 三大块独立演进 → 各 **System** 装 1 或多个 Component；**没有共部署多 Component 形态**（平台业务边界清晰，各 Component 通常 1:1 一个 binary / 一个 NPM 包 / 一个 SDK artifact）。

## 实例 B — 垂直产品（高尔夫模拟器，复杂业务 + 共部署多 Component + Flutter App + 多 System）

```
Domain: golf (垂直产品域，无父域)                  ← applications/golf 仓根（有 owning repo，monorepo）
├─ System: golf-backend     ← Composite Service：1 System + N service Component 共部署 1 binary 共享 kubernetes-id（详见 references/composite-service.md）
│   ├─ Component: golf-players (service, Go)        ← 8 业务域各自独立 Component，全部共享 backstage.io/kubernetes-id=golf-backend
│   ├─ Component: golf-play (service, Go)
│   ├─ Component: golf-share (service, Go)
│   ├─ Component: golf-course (service, Go)
│   ├─ Component: golf-achievement (service, Go)
│   ├─ Component: golf-environment (service, Go)
│   ├─ Component: golf-ai-coach (service, Go)
│   └─ Component: golf-sync (service, Go)
│      Phase 0 实现态: 仓内当前为单 binary 1 Component bootstrap，目标态按上方 8 Component 落 catalog（不论实现是 1 binary 还是未来拆 N binary，catalog 形态始终是 N Component 共享 kubernetes-id —— 业务边界先建好）
├─ System: golf-moments     ← 1 website Component（独立 nginx + CDN）
│   └─ Component: golf-moments (website, Vite + React 19) — 公开分享落地页 SPA
├─ System: golf-admin       ← 1 website Component（独立部署 + 独立 DB）
│   └─ Component: golf-admin (website, Next.js + Prisma) — B 端运营治理台
└─ System: golf-app         ← N 个 library Component（端 Flutter，无顶层 mobile-app shell Component）
    ├─ Component: golf-players-flutter (library)        ← 业务域 lib，对应 backend players 业务域
    ├─ Component: golf-play-flutter (library)
    ├─ Component: golf-aicoach-flutter (library)
    ├─ Component: golf-bridge-flutter (library)         ← Flutter↔Unity Pigeon + FFI
    ├─ Component: golf-design-system-flutter (library)
    ├─ Component: golf-recording-flutter (library)
    ├─ Component: golf-local-inference-flutter (library)
    ├─ Component: golf-device-flutter (library)
    ├─ Component: golf-telemetry-flutter (library)
    ├─ Component: golf-app-settings-flutter (library)
    └─ Component: golf-body-analysis-flutter (library)
    annotations:
      crashlytics.io/firebase-app-id-android: ...    ← mobile-app config 挂 System（无顶层 Component）
      apple.com/app-store-id: ...
      a4x.io/cicd-app-name: golf-app
```

读法（垂直产品的拓扑判断）：

| 设计抉择 | Golf 怎么选 | 为什么 |
|---|---|---|
| 父 Domain | **无**（`golf` 自己就是叶子 Domain）| 这是公司的一条独立产品线，不是公司能力域；不需要挂 BU 父域 |
| System 拆几个 | **4 个**：`golf-backend` / `golf-moments` / `golf-admin` / `golf-app` | 四块的消费者 / 鉴权机制 / 演进节奏 / 公开性显著不同；C 端 backend (JWT)、Web 落地页（无鉴权 + SEO）、B 端管理（Casdoor SSO + 独立 DB）、移动 App（IPA/APK）|
| `golf-backend` 内拆几个 Component | **8 个 service Component**（Composite Service：1 System + N Component 共享 `kubernetes-id`；详见 [`references/composite-service.md`](composite-service.md)）| 业务边界 SSOT 按 8 业务域切（players / play / share / course / achievement / environment / ai-coach / sync）；当前单 binary 共部署只是**部署形态**（共享 `kubernetes-id` + `a4x.io/cicd-app-name`）；未来若拆独立 Deployment，仅需改这两个注解，Component 拓扑 / OpenAPI / docs 结构都不动 |
| `golf-app` 内拆几个 Component | **N 个 library Component**（端业务边界清晰；Component 拆分 PRIMARY 判据 = 业务边界）| Flutter App 业务域独立可演进，library 类型按可独立 publish 的语义切分（即使代码暂未拆 melos） |
| `golf-app` 顶层为何没 mobile-app Component | shell 极薄（main + routes ~400 行）；按 Component 三条判据 ② / ③，shell 不值得独立 Component | mobile-app config (Crashlytics / App Store / Play / Sentry) 挂 System annotations |
| `golf-moments-web` 为什么不并入 `golf-backend` System | 物理边界不同（独立 nginx + CDN，share 业务域后端 SSR + 落地页 SPA 完全分离），消费者不同（社交爬虫 + 终端用户匿名访问），版本节奏与后端解耦 | System = 治理边界，三条都全 yes → 拆 |
| `golf-admin-web` 为什么独立 System | 独立 DB `golf_admin`（业务隔离 / 合规分级）、独立鉴权（Casdoor B 端 SSO）、独立技术栈（Next.js + Prisma 不是 Go）、独立部署节奏 | 与 backend 完全解耦的治理切片 |

垂直产品的特征：1 个 Domain 内多个 System 协作，**`golf-backend` 内部用 Composite Service**（1 System + 8 Component 共享 `kubernetes-id`）控制运维成本，同时给未来拆独立 Deployment 留好硬边界；**`golf-app` 内部用 N 个 library Component** 描述端业务边界，代码暂未按 melos 拆分（lib/ 单体）但 catalog 描述目标态。两者共同体现：**catalog 是业务边界 SSOT，不是部署形态 SSOT**。

## 实例 C — Flutter / 移动端 SDK 仓（无顶层 Component 形态）

引用 [engagement-sdk 实例](#实例-a--平台型产品增值业务跨多个产品线复用) 详细看：

- **System engagement-sdk** 装 3 个 library Component（`engagement-flutter-sdk` / `engagement-ios-sdk` / `cms-payment-plugin`）
- **没有顶层 SDK Component** —— SDK 是被宿主 App 嵌入的，不是用户安装的 IPA/APK
- 各 library Component 独立 publish 到对应 package registry（pub / CocoaPods / npm）

跟 Golf App 的区别：Golf 是 App（终端用户安装 IPA/APK，需要 mobile-app config），所以 `golf-app` System annotations 挂 Crashlytics / App Store ID；engagement-sdk 是 SDK（被嵌入），所以 System 没这些 annotations。

## 实例 D — 嵌入式固件 monorepo（C/C++/Rust）

```
Domain: <device-product>                            ← <hardware>/repo 仓根
└── System: <device>-firmware
    ├── Component: <device>-bsp (library)            ← 独立 publish 的 Conan recipe / cargo crate / Bazel target
    ├── Component: <device>-ble-stack (library)
    ├── Component: <device>-ota-client (library)
    ├── Component: <device>-sensor-driver (library)
    ├── Component: <device>-power-mgmt (library)
    └── (无顶层 firmware shell Component，main 极薄)
    annotations:
      memfault.io/project-id: ...
      ota-channel: stable
      dfu-bootloader-config: ...
      a4x.io/cicd-app-name: <device>-firmware
```

跟 Flutter App 同 case-by-case 矩阵：默认推荐路径 = 顶层固件镜像没 firmware Component（极薄 main），firmware config 挂 System annotations；只有「设备固件 + 厚 main（独立设备状态机 / 电源管理 / 安全模块独立成体系）」才建 1 个 firmware Component。

## 何时建顶层 mobile-app / firmware Component

参考 SKILL.md §1 的 case-by-case 三选矩阵：

| 场景 | 顶层 Component | config 挂哪 |
|---|---|---|
| **SDK / 库仓** | 无 | n/a |
| **App / 固件 + 极薄 shell**（默认推荐）| 无 | mobile-app/firmware config 挂 System annotations |
| **App / 固件 + 厚 shell**（独立体系，几千行以上）| 1 个 mobile-app / firmware Component | 挂这个 Component annotations |

判据：shell 跟业务 lib 的边界 + 独立演进可能。**强行为极薄 shell 建 Component 会让 "shell Component 一年不变更"**，违反 Component 三条判据的 ②③（接口契约清晰 / 独立演进可能）。
