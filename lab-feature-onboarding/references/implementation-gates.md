# 真实能力门禁实现

## 目录

- 完整调用面与 NH 同步门禁
- IOT/异步副作用门禁
- Flutter store、Lattice module 与真实入口
- 产品表现、Native 能力、分支和构建

## 1. 先画完整调用面

对每个新功能列出下表，任何空白都必须有“不适用”的代码依据：

| 调用面 | 示例 | 门禁位置 |
|---|---|---|
| 实验室设置 | list、toggle | NH experimental-features API + client store |
| 客户端真实入口 | 卡片按钮、三点菜单、名字角标、深链 | 渲染和 callback/route 两层 |
| 同步写 API | create、update、dismiss、correct | NH logic 开头、写入前 |
| 同步读 API | detail、list、timeline enrichment | NH logic/projector，禁用时清字段 |
| 异步入口 | MQ consumer、scheduler、webhook | 进入昂贵流程前 |
| 成本副作用 | enqueue、AI config、模型调用、结果写入 | IOT/执行服务的最后准入边界 |
| 历史/兼容接口 | 老 route、旧 app API、批量接口 | 与新接口同一 helper |

只在 UI 隐藏入口会被旧客户端、深链、缓存数据和直接 API 绕过。

## 2. NH 模式

当前公共 helper 的意图是：

```go
access, err := experimentalfeatures.CheckFeatureAllowed(ctx, svcCtx, featureKey)
if err != nil {
    return err
}
if !access.Allowed {
    return apierror.Forbidden("FEATURE_DISABLED")
}
```

`Allowed` 只能在 policy 合法且 `runtimeEnabled=true` 时为 true。明确的 policy deny 返回 `FEATURE_DISABLED`；missing config、PE error、timeout 等依赖异常同样不执行业务，但应保留 helper 的 typed error，便于客户端重试和故障定位。未知 lifecycle、错误字段类型等无效 policy 按 false。

同一请求投影多个实验功能时批量判断：

```go
accessByKey, err := labfeature.CheckFeaturesAllowed(ctx, svcCtx, []string{
    FeatureKeyOne,
    FeatureKeyTwo,
})
```

不要在 timeline 的每张卡片单独调用 PE。`CheckFeaturesAllowed` 会把远端错误降级为每个 key 的 `Allowed=false`/`remote_state_unavailable`，调用方先批量得到 snapshot，再据此：

- 跳过禁用功能的 enrichment 查询；
- 清除返回对象中受控的旧字段；
- 保留卡片公共字段和其他独立功能；
- 为 mutation 返回稳定业务错误。

搜索检查：

```bash
rg -n "CheckFeatureAllowed|CheckFeaturesAllowed|FEATURE_DISABLED" server
rg -n "<existing capability names and routes>" server
rg -n "Route:|Handler:|Consumer|Subscribe|cron|scheduler" server
```

仅当 NH 代码直接引用该 key 时增加常量；目录列表本身应由数据库动态返回。

## 3. IOT/异步服务模式

最终准入表达式：

```text
baseEligible(device, entitlement, business rules)
AND pePolicy(featureKey).runtimeEnabled
```

顺序必须是廉价判断在前、PE 最终判断在后；任何一步 false 都不执行副作用。

Java 形态示例：

```java
if (!baseEligibility.isEligible(userId, device)) {
    return DisabledReason.BASE_INELIGIBLE;
}

if (!labFeatureRuntimeGate.isEnabledForUser(userId, featureKey)) {
    return DisabledReason.LAB_POLICY_DISABLED;
}

return enqueueExpensiveTask(...);
```

`LabFeatureRuntimeGate` 应通过现有 `PersonalizationEngineService` 请求派生后的 `lab-<kebab>-policy`。parser 验证：

- JSON object；
- `version == 1`；
- lifecycle 在已知集合；
- `runtimeEnabled` 和 `allowsNewOptIn` 是 boolean。

不要：

- 从 lifecycle 推导准入；
- 调 NH check API；
- 读取 NH `lab_feature`；
- 直接读取 `user_feature_settings`；
- 用旧 GB eligibility 反向关闭用户已主动开启的 active 功能；
- 在没有失效协议时缓存最终 policy。

为 false、timeout、malformed、PE 5xx 分别写测试，断言 enqueue/AI call/result write 次数为 0。

## 4. Flutter 状态和真实入口

查找当前 package，而不是依赖固定 worktree 名：

```bash
rg -n "class LabFeatureKeys|class LabFeatureStore|LabFeatureSnapshot|canUse\(" \
  <workspace-root> --glob '*.dart'
```

新增 key：

```dart
abstract final class LabFeatureKeys {
  static const newFeature = 'new_feature';
}
```

真实入口读取 store：

```dart
final store = context.read<LabFeatureStore>();
final canUse = store.canUse(LabFeatureKeys.newFeature);

if (canUse) FeatureEntry(...);
```

同时保护 callback：

```dart
onTap: () {
  if (!store.canUse(LabFeatureKeys.newFeature)) return;
  openFeature();
},
```

页面应订阅 `store.changes` 或既有响应式封装，使 toggle 成功后的 snapshot 立即重建；必要时刷新 timeline/detail 数据。不要要求重启 App 或重新登录才生效。

首次进入实验室只展示 NH 返回的计算状态，不应触发 toggle 或创建 PE 用户设置。默认 ON/OFF 都由最终 policy 投影到 `feature.enabled`；只有用户切到与当前计算状态不同的值时，NH 才懒写显式选择。

`LabFeatureSnapshot.canUse` 对未知 key 返回 false。Timeline 中“没有注册 store 时保留旧行为”的 fallback 只用于尚未接入实验室的旧宿主；KB、VN 等已支持实验室的宿主必须成功 bootstrap `LabFeatureModule`，不能把 store 缺失视为允许实验功能的授权路径。

必须检查的客户端触点：

- `LabFeatureKeys` 和 unknown-key 默认 false；
- 实验室 list/card、title/description mapping、CMS icon；
- `_featureSortOrder` 或当前排序实现；
- 实际入口显示；
- 点击/路由/深链；
- 页面返回后状态；
- 数据缓存和 timeline reload；
- Android/iOS 宿主是否正确注册 store/route；
- 每个客户端 allowlist 外的功能是否同时从 list、summary/red dot、update 消失；
- 未接入客户端和其他品牌是否保持旧行为。

多客户端 service 形态：

```dart
final service = LabFeaturesService(
  clientConfig: LabFeatureClientConfig.fromBrand(brand),
);
```

不要在 Skill 中把某个 App 当前支持的 feature key 集合写死为长期事实。接入时从产品确认的新支持矩阵、目标 release 锁定的 Nexus package、`LabFeatureClientConfig` 和对应测试四处交叉核对。显式 allowlist 可以避免服务端新增目录行后旧客户端提前展示未实现功能；不要为了省事把子集客户端改为 `null`，也不要用扩展前的旧 allowlist 否定本次明确新增的功能。

不要只在 Widget 层 `where`。否则服务端 summary 仍可能让客户端出现红点，深链仍可向 allowlist 外 key 发 toggle。子集客户端应从过滤后的可见列表重算 summary 和最新发布时间。

列表过滤和服务端可见性是两层独立条件：客户端 allowlist 决定该版本是否支持某 key；NH 的 `visibleInLab` 决定当前用户是否展示。若同一个 allowlist 内功能在显式 OFF 后消失，先读取接口中的 `gbEligible`、`runtimeEnabled`、`visibleInLab`，并核对 GrowthBook 目标 App eligibility，不要直接归因于 Flutter 首次加载和 update 回包使用了不同过滤器。

## 4.1 Lattice module 边界

实验室设置页是完整的 Lattice feature module：

```text
lattice://lab-features
  -> 通用 FlutterBoost/Lattice adapter
  -> ModuleRegistry.resolveRoute('/lab-features')
  -> LabFeatureModule
```

`LabFeatureModule` 统一负责：

- `LabFeatureRoutes.root` 和兼容 legacy route；
- `LabFeaturesService`、`LabFeatureStore` 的 DI；
- `LabFeatureNoticeCoordinator`；
- `LabFeatureNoticeBridge.refreshRequests` 订阅；
- module dispose 时取消订阅并反注册 DI。

G0 Flutter 只构造 `LabFeatureClientConfig`、注入 `BoostLabFeatureNoticeBridge` 并保留通用 `KiwibitBoostDelegate`。新增功能不得在 G0 的 `main.dart`、页面壳或 `lattice_main.dart` 新增 Lab-specific route、store、event listener 或 lifecycle。

## 5. 产品表现边界

“能力关闭”不总等于“整个 UI 区域消失”。先从 PRD/Figma/产品确认 capability boundary。

现有功能可用于理解差异：

- Name The Bird：开时三点菜单出现命名项并可完成命名；关时命名项和 API 均不可用。
- Video ID Coach：开时鸟名旁角标/入口可进入教练详情；关时入口和返回的 coach enrichment 不可用。
- Bird Story：开时会员可生成/看到 Story 内容；关时停止生成并隐藏 Story body。卡片底部会员三点或非会员 paywall icon 属于既有产品 chrome，按产品决定保留，不能整块删除。

新功能必须写出同等精度的“保留什么、移除什么、后端停止什么”。

## 6. Native 能力

同一 App 已有实验室设置入口时，仅新增目录记录通常不需要再次改 Android/iOS。一个新 App/tenant 首次接入实验室时，Android/iOS 仍需增加 flavor 限定的设置入口、图标、红点桥接、Flutter 路由和入口埋点。KB 与 VN 当前都属于 Nature App，宿主共享 `isNatureApp`/`isNature` 判断；具体功能差异放在 `LabFeatureClientConfig.allowedFeatureKeys`，不要再叠加 tenant ID 集合。Native 统一打开 `lattice://lab-features`，旧 `flutter_lab_features_page` 只作为兼容别名。如果真实能力在 native，还要：

1. 从共享 store 建立明确桥接，或调用 NH check API。
2. 把 unknown/missing/error 视为 false。
3. 监听状态变化并更新 UI。
4. 对 deep link、notification route 和恢复页面做二次检查。
5. 红点/未读状态只负责提示，不作为授权。

NH client package 与 Android/iOS/Flutter module 不是同一责任层：NH client 负责 Timeline 中真实功能入口和数据展示门禁；三个客户端 MR 主要负责设置页入口、实验室页面和桥接。发布前必须分别核对，不能用其中一个 MR 代替另一个。

## 7. 分支与构建

- 服务端从当前 staging 集成分支建立 worktree；先 fetch/rebase 并检查未提交内容。
- 同一个 App 的 Android/iOS 宿主和 Flutter module 必须来自同一 release 系列；KB 与 VN 分开读取 source/target，不跨系列 cherry-pick 后直接提 MR。
- 核对 Android 嵌入的 Flutter commit，不能只看分支名。
- Naturehood/Lattice package 必须先按 package lifecycle 发布到 Nexus；G0 Flutter `pubspec.yaml` 和 lockfile 使用同一批新 hosted 版本，不能保留本地 path/git 依赖。prerelease 版本应相对目标 release 单调递增，但不能在 SOP 中硬编码某个历史 `.poc.N`。
- 不修改其他 agent/用户的 dirty worktree；必要时创建独立 worktree。
- 真机 Android 用 `adb install -r` 保留登录态；安装前记录 versionName/versionCode、git SHA 和 Flutter SHA。
- crash 时先采集 `adb logcat`/Sentry stack，再判断是 release 基线还是本次变更。
