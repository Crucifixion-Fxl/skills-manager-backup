# 端到端开发完整指引（从需求到上线，跨多仓）

> 本文用一个**真实但简单的跨仓需求**走完所有步骤，新人照做一遍，第一周即可完成 feature MR 并合入。
>
> Agent 用法：用户问"新需求怎么做" / "完整流程是什么" / "跨仓改动怎么走"时，先给本文件**目录索引**，再按用户卡住的环节展开细节。

---

## 案例：VIP 接口新增"剩余天数"字段，三端打通

### 需求背景

PM 提需求：**用户在个人中心看 VIP 状态时，需要显示"剩余 X 天"**。
当前接口只有 `expireTime`（时间戳），客户端要自己算剩余天数。希望后端直接算好下发，避免三端各算一次（容易不一致）。

### 跨仓涉及（4 个仓 + 1 个埋点配置）

| 仓 | 改动 | 工作量 |
|----|------|--------|
| `CLOUD/iot-service-unified` | DTO 加 `remainingDays` 字段 + Controller 计算 + 单测 | M |
| `platforms/lattice`（Flutter）| API 模型加字段 + UI 展示 + 单测 | S |
| `SWCLIEN/g0-android` | Bridge 协议 + 埋点 | S |
| `SWCLIEN/g0-ios` | Bridge 协议（同步 Android）+ 埋点 | S |
| `data/tracker-management` | 注册新埋点事件 `vip_remaining_days_view` | S |

**业务域归属**：增值业务 / 权益中心（汪强）→ 客户端展示（lattice scope tiancailaxi 主导）

---

## 第 1 步：需求 / Issue 录入（跨仓 issue 主单 + 子 issue）

**目标**：把跨仓需求拆成主 issue + 各仓子 issue 便于追踪。

**做什么**：

```bash
# 1. 主 issue 落到推动方仓（这里是 iot-service-unified，因为字段定义从后端起源）
glab --repo CLOUD/iot-service-unified issue create \
  --title "[VIP] expireTime 接口加 remainingDays 字段（三端打通）" \
  --description "..."

# 2. 各端子 issue（主 issue 描述里 link 它们）
glab --repo platforms/lattice issue create \
  --title "[VIP] Flutter 消费 remainingDays 字段并展示" ...
glab --repo SWCLIEN/g0-android issue create \
  --title "[VIP] Android Bridge 协议同步 + 埋点" ...
glab --repo SWCLIEN/g0-ios issue create \
  --title "[VIP] iOS Bridge 协议同步 + 埋点" ...
```

**质量门**：
- ✅ 主 issue 在"字段定义起源仓"（这里 iot-service-unified）
- ✅ 主 issue 描述 link 所有子 issue + 各仓 owner
- ✅ 每个子 issue 标注依赖关系（如"等主 issue iot-service MR 合入再开工"）
- ✅ Title 含品牌 / 模块前缀，类型一致（feat / fix）

→ 子 skill：`gitlab-issue-sop`

---

## 第 2 步：方案设计（跨仓最重要）

**目标**：跨仓字段格式 + 协议必须先对齐，避免后端先合后改不动客户端。

**做什么**：

1. 起一个 RFC（写入主 issue 评论 / 飞书云文档），**列三端约定**：

   ```yaml
   # API 字段（iot-service-unified DTO）
   field: remainingDays
   type: int (天数，向下取整；过期/未订阅返回 0；永久 VIP 返回 -1)
   placement: VipResponse.data.remainingDays
   nullable: false
   default: 0
   
   # Bridge 协议（Android / iOS → Flutter）
   action: getVipInfo
   response.remainingDays: int
   
   # Flutter 模型
   class VipInfo { int remainingDays; ... }
   
   # 埋点
   event: vip_remaining_days_view
   props: { remaining_days: int, vip_tier: string }
   ```

2. 在主 issue 找 owner 确认（**跨仓需求至少 2 位 owner LGTM**）：
   - 后端 owner：汪强（权益中心）
   - 客户端 owner：tiancailaxi（lattice scope SPM）+ 林智（g0-android/ios）
   - 数据：汪洋（埋点 owner）— 确认埋点字段命名规范

3. **关键约束**确认：
   - 永久 VIP 用 `-1` 还是 `null` 还是某个魔数？→ 选 `-1`（避免 null 处理）
   - 时区怎么算？→ 后端按用户时区算 + 客户端只展示
   - 缓存策略？→ 接口 60s 缓存（与现有 VIP 接口一致）

**质量门**：
- ✅ 三端字段 / 类型 / 协议完全对齐（不允许 Android `int`、iOS `Int`、Flutter `int?` 不一致）
- ✅ Owner 显式 confirm（不允许"应该 OK"猜测）
- ✅ 边界条件穷举（永久 / 过期 / 未订阅 / null）
- ✅ 不向后兼容的字段必须开 AB 实验灰度（这次是新增字段不影响兼容，可以直接发）

→ 子 skill：`architect` / `brainstorming` / `lattice-native-bridge`（如改 Bridge 协议）

---

## 第 3 步：起 worktree + 各仓 feature 分支

**目标**：每个仓单独 worktree，避免一个仓改动污染其他仓。

> 路径占位符说明：本文件以 `<workspace>` 作为本地工作区根目录的占位符。
> 你的环境中按实际路径替换（常见：`~/Work/Projects/`、`/data/repos/` 等）。

**做什么**：

```bash
# 后端
cd <workspace>/gen3/iot-service
git worktree add ../iot-service-wt-vip-remaining -b feat/vip-remaining-days
cd ../iot-service-wt-vip-remaining

# 客户端 lattice
cd <workspace>/lattice/lattice
git worktree add ../lattice-wt-vip-remaining -b feat/vip-remaining-days
cd ../lattice-wt-vip-remaining

# Android
cd <workspace>/lattice/g0-android
git worktree add ../g0-android-wt-vip-remaining -b feat/vip-remaining-days

# iOS
cd <workspace>/lattice/g0-ios
git worktree add ../g0-ios-wt-vip-remaining -b feat/vip-remaining-days
```

**质量门**：
- ✅ 各仓独立 worktree（lattice 强制 / g0-android 推荐 / iot-service 可选）
- ✅ 分支名跨仓统一：`feat/vip-remaining-days`（便于 cross-link）
- ✅ 各仓基于正确 base（main / release/* 视情况）

→ 子 skill：`using-git-worktrees` / `multi-worktree-dev`

---

## 第 4 步：后端先行（TDD RED → GREEN）

**目标**：跨仓改动从字段起源仓先做，后端字段定义稳定后客户端才有依据。

**做什么**：

1. **TDD RED**：在 iot-service-unified 写测试

   ```java
   @Test
   void getVipInfo_returnsRemainingDays_whenNotExpired() {
       // Arrange
       Vip vip = new Vip();
       vip.setExpireTime(Instant.now().plus(7, DAYS).getEpochSecond());
       
       // Act
       VipResponse response = vipService.getVipInfo(userId);
       
       // Assert
       assertEquals(7, response.getData().getRemainingDays());
   }
   
   @Test
   void getVipInfo_returns_negative1_whenForever() { ... }
   
   @Test
   void getVipInfo_returns_0_whenExpired() { ... }
   
   @Test
   void getVipInfo_returns_0_whenNotVip() { ... }
   ```

2. 跑测试 → **必须 RED**：
   ```bash
   cd <workspace>/gen3/iot-service-wt-vip-remaining
   ./mvnw test -Dtest=VipServiceTest
   # → FAILED: getRemainingDays() 不存在
   ```

3. **TDD GREEN**：实现 `VipResponse.Data` 加 `remainingDays` 字段 + `VipServiceImpl` 计算逻辑

   ```java
   public class VipResponse.Data {
       // ... existing fields
       private int remainingDays;
   }
   
   // VipServiceImpl
   private int computeRemainingDays(Vip vip) {
       if (vip == null || !vip.isActive()) return 0;
       if (vip.getExpireTime() == FOREVER) return -1;
       long now = Instant.now().getEpochSecond();
       long secs = vip.getExpireTime() - now;
       return secs <= 0 ? 0 : (int)(secs / 86400);
   }
   ```

4. 跑测试 → **必须 GREEN**：
   ```bash
   ./mvnw test -Dtest=VipServiceTest  # → ALL PASSED
   ./mvnw verify  # → 完整 build + 集成测试
   ```

5. 更新 API 文档（OpenAPI / Swagger）

**质量门**：
- ✅ 4 个边界 case 都有测试（正常 / 过期 / 未订阅 / 永久）
- ✅ 单测覆盖率 ≥ 80%（看 JaCoCo report）
- ✅ 没有 Thread.sleep / 真实网络（iot-service 单测规则）
- ✅ Build 无 warning
- ✅ DDD 分层：computeRemainingDays 在 domain-service 层，DTO 字段在 domain-interface

→ 子 skill：`tdd-workflow` / `springboot-tdd` / `springboot-verification`

---

## 第 5 步：后端 stage 部署 + 验证

**目标**：后端字段先上 stage，客户端有真实接口可联调。

**做什么**：

1. 起 stage MR：
   ```bash
   glab mr create \
     --title "feat(vip): add remainingDays field [AI-Generated]" \
     --target-branch master_for_stage \
     --description "..."
   ```

2. CI 全绿 → 合 stage MR → ArgoCD 自动 sync 到 stage 集群

3. **验证**：用 curl / Postman 打 stage 接口
   ```bash
   curl -X POST 'https://api-stage-us.kiwibit.com/vip/info' \
     -H 'Authorization: Bearer <token>' \
     -d '{"app":{"bundle":"com.kb.kiwibit",...}}'
   # 期望返回中含 "remainingDays": 7
   ```

4. 通知客户端 dev 可以联调：
   - 飞书 @lattice / @android / @ios dev：`stage 接口已就绪，可以开工客户端`
   - 在主 issue 评论 link 到 stage MR

**质量门**：
- ✅ Stage 接口实测返回字段正确（不光是单测过）
- ✅ Stage 监控观察 5-10 分钟无 error 飙升
- ✅ 4 个边界 case 在 stage 都验证（造测试账号）

→ 子 skill：`argocd` / `troubleshooting`（接口异常排查）

---

## 第 6 步：客户端三端并行开发

**目标**：lattice / Android / iOS 三端并行接入新字段（可分配给不同 dev 同时做）。

**做什么**：

### 6.1 Lattice（Flutter）

```dart
// packages/vip_api/lib/vip_api.dart - 接口定义
abstract class VipApi {
  Future<VipInfo> getVipInfo();
}

class VipInfo {
  final int remainingDays;  // 新增
  // ... existing fields
}

// packages/vip_feature/lib/vip_feature.dart - 实现
final remainingDays = response['remainingDays'] as int? ?? 0;

// 单测先行：
test('VipInfo parses remainingDays', () { ... });
```

**质量门**：
- ✅ `vip_api` 包只放接口（`*_api` 纯净规则，dep_checker 强制）
- ✅ 单测在 `*_feature` 包内
- ✅ `dart analyze` + `dart format` + `dep_checker` 全过

### 6.2 Android（g0-android）

```kotlin
// 如果 Android 走 Flutter 模块，本端只需 Bridge 协议字段透传
// VipInfoResponse.kt
data class VipInfoResponse(
    val remainingDays: Int,
    // ...
)

// 埋点
viewLifecycleOwner.lifecycleScope.launch {
    val vipInfo = vipApi.getVipInfo()
    SnowplowTrackHandler.track(
        "vip_remaining_days_view",
        mapOf("remaining_days" to vipInfo.remainingDays, "vip_tier" to vipInfo.tier)
    )
}
```

**质量门**：
- ✅ Bridge 协议字段类型与 iOS 一致
- ✅ 埋点字段命名与数据团队规范一致（snake_case）
- ✅ 双端一致性：commit message 标 `需同步到 iOS`

→ 子 skill：`kotlin-patterns` / `lattice-native-bridge`

### 6.3 iOS（g0-ios）

```swift
// 同 Android 改动，Swift 实现
struct VipInfoResponse: Codable {
    let remainingDays: Int
    // ...
}

// 埋点（同名 + 同字段）
SnowplowTrackHandler.shared.track(
    eventKey: "vip_remaining_days_view",
    props: ["remaining_days": vipInfo.remainingDays, "vip_tier": vipInfo.tier]
)
```

**质量门**：
- ✅ 字段类型 + 埋点 key/props 与 Android 严格一致

### 6.4 数据 BI（data/tracker-management）

注册埋点 `vip_remaining_days_view`：
- 事件名 / 字段定义 / 触发时机
- 在 tracker-management 后台录入或飞书联系汪洋

→ 子 skill：`tracking-lifecycle`

---

## 第 7 步：模拟器 + 真机验证（三端联调）

**目标**：三端都连 stage 后端验证字段对得上。

**做什么**：

```bash
# Android 模拟器
adb install -r app/build/outputs/apk/<flavor>/debug/<apk>.apk
# 启动 app → 个人中心 → 看 VIP 卡片是否显示"剩余 X 天"
# logcat 抓埋点
adb logcat -d | grep -iE "vip_remaining_days_view|remainingDays"

# iOS 模拟器
xcrun simctl install booted <app>.app
xcrun simctl launch booted com.kb.<brand>
# 同样验证显示 + 抓 console 看埋点
```

**质量门**：
- ✅ 三端展示数字一致（同账号同时刻看到的"剩余 X 天"必须相同）
- ✅ 埋点上报真实可见（Snowplow 测试 endpoint 看上报记录）
- ✅ 边界测试：
  - 测试账号 A（VIP 还有 3 天）：显示"剩余 3 天"
  - 测试账号 B（已过期）：显示"已过期"
  - 测试账号 C（永久 VIP）：显示"永久"
  - 测试账号 D（未订阅）：不显示该卡片

→ 子 skill：`local-fullstack-debug` / `e2e-testing` / `qatools`（造测试账号）

---

## 第 8 步：各仓 Commit + MR（按依赖顺序）

**目标**：MR 合入有依赖关系，必须按"后端 → 客户端"顺序。

**做什么**：

### 8.1 后端 release MR

```bash
# stage MR 验证 OK 后起 release MR
cd <workspace>/gen3/iot-service-wt-vip-remaining
glab mr create \
  --title "feat(vip): add remainingDays field" \
  --target-branch release/master \
  --description "## 关联
- 主 issue: CLOUD/iot-service-unified#XXX
- 子 issue: lattice#XXX g0-android#XXX g0-ios#XXX
- Stage MR: !YYY (verified)
- 飞书 SOP: https://a4x-paas.feishu.cn/wiki/R9wiwomPyif9PKkbpp1cMYNRnK1"
```

### 8.2 lattice MR（等后端 release MR 合入后）

依赖后端字段稳定才能合，否则 release 时客户端先发但接口没字段会引发 NPE。

```bash
cd <workspace>/lattice/lattice-wt-vip-remaining
glab mr create \
  --title "feat(vip): consume remainingDays from API"
```

### 8.3 Android + iOS MR（同步）

两端 MR **同时起**、同时 merge（双端一致性强制）。
两个 MR 在 description 里互相 link：`双端 MR: g0-ios!XXX`。

**质量门**：
- ✅ MR 顺序：后端 → 客户端（避免客户端先合接口未上）
- ✅ 双端 MR 同步：Android + iOS 同时合
- ✅ 每个 MR description 含飞书 wiki 或 GitLab 文档链接（便于 reviewer 对照需求与设计）
- ✅ 主 issue 自动随各仓 MR 合入而完成

→ 子 skill：`gitlab-mr` / `code-submit` / `requesting-code-review`

---

## 第 9 步：CI 全绿 + Code Review

**目标**：每个仓的 CI + review 都过。

**做什么**：

```bash
# 每个仓
glab ci status   # 全绿
# 若有失败：
glab ci trace <job-id>
```

按业务域 owner 找 reviewer：
- 后端 MR：reviewer = 汪强（权益中心 owner）+ 同仓架构关注人
- lattice MR：reviewer = tiancailaxi（SPM）+ 林智
- Android / iOS MR：reviewer = 林智（双端一致 + AB / 埋点）

**质量门**：
- ✅ 各仓 CI 全绿
- ✅ 每个 MR 至少 1 位 reviewer LGTM
- ✅ 无 unresolved discussion

→ 子 skill：`receiving-code-review` / `code-review` / `lattice-quality-gate`

---

## 第 10 步：合入 + 触发 CD（按依赖顺序）

**目标**：按依赖合入 + 部署 + 灰度。

**做什么**：

1. **后端先合**：
   - 后端 release MR 合入 → ArgoCD sync prod → 看监控 20min（按"上线规范 12 步"）
   - 部署遵循"禁周五 / 禁晚 6 点后 / 禁无人值守"
   - 实时验证 prod 接口返回字段

2. **客户端跟随**（按 App 提审值班）：
   - lattice 模块包 release（pubspec.yaml 升版）
   - g0-android 跟版本提审：陈志超（KB / VH 提审值班）
   - g0-ios 跟版本提审：陈志超
   - iOS 自动上传应用商店 → 手动提审；Android 打 apk/aab → 手动提审

3. **客户端灰度**：
   - 用 GrowthBook flag `vip_remaining_days_enabled` 灰度（先 5% → 50% → 100%）
   - 每阶段观察 24h 无异常再放量
   - 子 skill：`cicd-gray-whitelist`（后端服务侧灰度）/ `growthbook`（客户端 flag）

**质量门**：
- ✅ 后端先 100% 上线再放客户端
- ✅ 客户端走灰度（不直接全量）
- ✅ 每阶段 prod 监控 + 埋点回流验证

→ 子 skill：`argocd` / `argocd-deploy` / `gitlab-ci` / `cicd-gray-whitelist` / `growthbook`

---

## 第 11 步：上线后跟进（跨仓监控）

**目标**：跨仓需求上线后从多角度观察。

**做什么**：

1. **后端监控**：
   - Prometheus + Grafana 看 `/vip/info` 接口 P95 / 错误率
   - SLA 告警：`ratio_backend_pay_failed` 等关联 VIP 指标
   - Sentry / Bugsnag 看 VipServiceImpl 是否有新异常

2. **客户端监控**：
   - Sentry + Bugsnag 看 VIP 模块崩溃率
   - logcat / iOS console 看埋点上报正常

3. **数据回流**：
   - 24h 后 Superset 看埋点 `vip_remaining_days_view` 上报量
   - DataHub 看新字段 `remaining_days` 进数仓

4. **关闭 issue**：
   ```bash
   glab issue close <主 issue> --comment "已合入：iot-service !XXX / lattice !YYY / g0-android !ZZZ / g0-ios !WWW，prod 验证 OK，埋点回流正常。"
   ```

5. **总结报告**（lattice / g0-android 强制）：
   - 写 `docs/reports/vip-remaining-days-2026-05-09.md`
   - 含：跨仓 MR 列表 / 上线时间线 / 灰度阶段 / 出现的问题 / 学到的教训

**质量门**：
- ✅ 上线后 24h 持续看监控
- ✅ 主 issue + 各子 issue 全部 close
- ✅ 写总结报告（跨仓需求必写）

→ 子 skill：`sla-alert-analysis` / `tracking-lifecycle` / `superset` / `sentry`

---

## 完整流程检查清单（跨仓版）

```
□ Step 1  跨仓 issue 主单 + 子 issue（gitlab-issue-sop）
□ Step 2  方案设计 + 三端协议对齐（architect / lattice-native-bridge）
□ Step 3  各仓独立 worktree（using-git-worktrees）
□ Step 4  后端 TDD RED → GREEN（springboot-tdd）
□ Step 5  后端 stage 部署 + 验证（argocd / troubleshooting）
□ Step 6  客户端三端并行开发（kotlin-patterns / dart-flutter-patterns / swiftui-patterns）
□ Step 7  三端联调（local-fullstack-debug / qatools）
□ Step 8  各仓 MR（按依赖顺序：后端 → 客户端）（gitlab-mr）
□ Step 9  各仓 CI + Review（code-review）
□ Step 10 合入 + 部署 + 灰度（argocd / cicd-gray-whitelist / growthbook）
□ Step 11 上线观察 + 总结（sla-alert-analysis / sentry）
```

---

## 跨仓需求的关键陷阱（必避）

| 陷阱 | 怎么避 |
|------|--------|
| 协议不一致：Android `int` / iOS `Int?` / Flutter `int?`，运行时崩 | 第 2 步 RFC 严格定义类型 + 各端 nullable / default 一致 |
| 后端接口未上客户端先合 | 第 10 步严格按"后端 → 客户端"顺序合入 |
| 双端不同步：Android 合了 iOS 漏了 | 第 8 步双端 MR 同时起、互相 link、同时合 |
| 灰度漏配 / 全量直接放 | 第 10 步用 GrowthBook flag 强制灰度 |
| 边界 case 漏：永久 / 未订阅 / 跨时区 | 第 2 步穷举边界，第 4 步每个 case 写测试 |
| 埋点字段命名不规范 | 第 6.4 步找数据团队（汪洋）规范命名 |
| 跨仓 MR 关联断了 → issue 关不掉 | 各 MR description 互 link，主 issue 列表追踪 |

---

## 不同跨仓场景的差异

| 场景 | 主要差异 |
|------|---------|
| 跨 2 仓（前后端）| 简化：跳过双端同步；MR 顺序仍是后端先 |
| 跨 3 仓（lattice + Android + iOS 客户端）| 客户端三端必须双端一致；走 Lattice Native Bridge |
| 跨 4+ 仓（含数据 / DevOps）| 加埋点注册 + AB 实验 + 监控告警配置 |
| 涉及向下不兼容 | 必须开 GrowthBook flag + 全量灰度 + 多版本兼容期 |
| 紧急跨仓 hotfix | 跳过部分步骤；上线必须双人审批（牧云 + 江领）|
| 涉及私密数据（PII / 密钥）| 加 Vault 配置 + 更新 secret-rotation-plan.md |

---

## 子 skill 总索引（按步骤）

> **可调用** = 本仓 engineering/skills 真实存在，agent 用 Skill 工具进入。
> **仅参考** = 外部 plugin / superpowers / lattice 内独立 skill，agent 当作"说明性引用"，不要尝试 Skill 工具调用。

| Step | 可调用 skill（本仓）| 仅参考 skill（外部）|
|------|------------------|------------------|
| 1 录 issue | `gitlab-issue-sop` | — |
| 2 方案 | `architect` | `brainstorming` / `lattice-native-bridge` |
| 3 分支 | — | `using-git-worktrees` / `multi-worktree-dev` |
| 4 TDD（按栈）| — | `springboot-tdd` / `tdd-workflow` / `lattice-testing` / `kotlin-testing` |
| 5 stage 部署 | `argocd` / `troubleshooting` | — |
| 6 客户端开发 | — | `kotlin-patterns` / `swiftui-patterns` / `dart-flutter-patterns` / `lattice-native-bridge` / `lattice-capability` |
| 6.4 埋点 | `tracking-lifecycle` | — |
| 7 联调 | `local-fullstack-debug` / `mock-engine` / `qatools` | `e2e-testing` |
| 8 提交 | `code-submit` / `gitlab-mr` | `requesting-code-review` |
| 9 review | `code-review` | `receiving-code-review` / `lattice-quality-gate` |
| 10 部署 + 灰度 | `argocd` / `argocd-deploy` / `gitlab-ci` / `growthbook` | `cicd-gray-whitelist` |
| 11 观察 | `sla-alert-analysis` / `tracking-lifecycle` / `superset` / `prometheus` / `grafana` / `sentry` | — |

---

## 数据来源

- 真实需求模板：跨端 VIP 状态字段打通（增值业务常见）
- 飞书"上线规范"12 步 SOP：https://a4x-paas.feishu.cn/wiki/R9wiwomPyif9PKkbpp1cMYNRnK1
- 飞书"APP 版本值班计划"：https://a4x-paas.feishu.cn/wiki/E5b8wJbRJiRMe3k9I9mcWRFKnxh
- ownership.md（业务域 owner）
- glossary.md（P95/P99/SLA 等指标定义）
