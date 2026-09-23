# 各端埋点实现规范

> 本文档描述各端埋点的注入规范和约定。SDK API 签名详见 [sdk-api.md](sdk-api.md)。
> 最后更新：2026-04-08

---

## Flutter (g0-flutter-module)

### 注入位置

| 事件类型 | 注入位置 | 说明 |
|---------|---------|------|
| PV | `onPageShow()` 生命周期 | 需 mixin `PageVisibilityObserver`，每次页面可见时触发 |
| EXP（静态元素） | `build()` / `initState()` / 条件构建方法中 | 需防重标志 `bool _xxxExposureTracked = false`，首次渲染时上报 |
| EXP（列表元素） | `VisibilityDetector.onVisibilityChanged` | `visibleFraction >= 0.5` 时触发 |
| CLK | `GestureDetector.onTap` / `InkWell.onTap` | 在业务逻辑和页面导航之前上报 |
| SELF_EVENT | 业务状态变化时 | 视频播放状态变化、滚动加载、后台切换等场景 |

### 防重机制

| 场景 | 方式 |
|------|------|
| PV | `onPageShow()` 生命周期自动控制 |
| 静态元素 EXP | `bool` 标志位（如 `_storyExposureTracked`） |
| 列表 EXP | `VisibilityDetector` + `visibleFraction` 阈值 |
| 自定义事件 | `Set<String>` 记录已上报的 traceId |
| 列表刷新 | `resetForRefresh()` 时清空防重集合，允许重新上报 |

### 约定

- SPM 层级通过 `Event` 构造函数的 `page` / `module` / `component` 参数传入，无需手动拼接
- 跨页面 SPM 传递：通过路由参数传入 `spmB`，在 `initState()` 中调用 `NormalTracker.track(event, preSpm: spmB)`
- 每个功能模块一个 Tracker 类，文件位置：`lib/pages/{feature}/tracker/{feature}_tracker.dart`
- 上报调用链：Tracker 静态方法 → `NormalTracker.track()` → `TrackerChannel.track()` → 原生 Method Channel
- 后台切换处理：在 `didChangeAppLifecycleState(AppLifecycleState.paused)` 中上报需要结束的事件（如视频播放结束）
- 动态组件名：允许用字符串拼接生成 component（如 `'locked_$source'`），但 page 和 module 必须是固定字符串

---

## Android 原生 (g0-android)

### 注入位置

| 事件类型 | 注入位置 | 说明 |
|---------|---------|------|
| PV | Activity `onCreate` / Fragment `onViewCreated` | 通过 `setPageTrackNode()` 绑定后 `postTrack(SpmEventType.PV)` |
| EXP | RecyclerView Adapter `convert()` 中 | 必须用 `View.Tag` 防重 |
| CLK | `setOnClickListener` 或按钮回调 | `view.postTrack(SpmEventType.CLK)` 或便捷方法 `clkTrack()` |
| SELF_EVENT | 业务逻辑完成后 | 使用 `postSelfEventTrack(eventKey, params)` |

### SPM 绑定方式

| 方式 | 适用场景 |
|------|---------|
| `activity.trackNode = PageTrackNode(KEY_SPM_B to "page")` | Activity 页面级（推荐） |
| `view.trackNode = TrackNode { params -> ... }` | View 属性绑定 |
| `view.setTag(R.id.tag_track_node, ...)` | Java 中通过 Tag 绑定 |
| `fragment.referrerNode = TrackNode { ... }` | 跨页面回源参数传递 |

### 防重机制

| 场景 | 方式 |
|------|------|
| PV | 页面可见即发送，从其他页面返回时也重发 |
| 列表 EXP | `view.getTag(R.id.xxx_track_posted)` + `setTag(true)` 标记已上报 |
| SELF_EVENT | 业务方自行维护上报状态 |

### 约定

- PV 时序：PV 必须先于页面其他模块的 EXP/CLK 埋点发送
- TrackNode 层级继承：`collectTrack()` 从当前 View 向上遍历 View 树，按根到叶顺序填充参数，后者可覆盖前者
- SPM 自动补齐：spmD 为空时自动填充 0；spmB 为空但 spmC 不空时 Debug 下 Toast 警告
- SPM 参数值不能包含 `.` 符号
- 便捷方法：`clkTrack()` / `clkFullTrack()` / `expFullTrack()` / `pvFullTrack()`（定义在 GlobalSwap.kt）
- 集成 `tracker_desensitization` 模块自动脱敏（支持 MASK / CLEAN / HASH / EMAIL / MACADDRESS / IP 六种规则）
- Snowplow 本地 Schema 验证：Debug 模式下 `TrackValidationHook` 自动拦截不合规埋点，Release 可选启用

---

## iOS 原生 (g0-ios)

### 注入位置

| 事件类型 | 注入位置 | 说明 |
|---------|---------|------|
| PV | `viewDidLoad()` 中调用 `self.track(spmB:)` | 自动创建 PageTrackNode 并发送 PV |
| EXP（静态 View） | `viewWillAppear` 或业务方法中 | 创建临时 `UIView()` 调用 `view.track(spmType: .exp, params: [...])` |
| EXP（Cell） | `tableView(_:willDisplay:forRowAt:)` | Cell 即将显示时，自动继承 VC 的 trackNode |
| CLK（按钮） | `@IBAction` 或 `onTap` 回调 | `view.track(spmType: .clk)` 或 `view.vipTrack(spmType: .clk, ...)` |
| CLK（Cell） | `tableView(_:didSelectRowAt:)` | Cell 被点击时 |
| SELF_EVENT | 业务逻辑完成后 | 通过 `TrackerManager.shared.trackNonSpmEvent(eventName:data:)` 上报 |

### SPM 绑定方式

| 方式 | 适用场景 |
|------|---------|
| VC: `self.track(spmB: "page_name")` | ViewController 页面级 PV（自动创建 PageTrackNode） |
| View: `view.trackNode = TrackNode(params: [...])` | 关联对象绑定（`objc_setAssociatedObject`），子 View 自动继承 |
| View: `view.track(spmType:params:)` | 单次上报，不需要继承 |
| 跨页面: `alert.trackNode = self.trackNode?.copy()` | 通过 NSCopying 复制 TrackNode 传递上下文 |

### 防重机制

| 场景 | 方式 |
|------|------|
| PV | 页面重新进入时也会重发，无需防重 |
| Cell EXP | `willDisplay` 配合业务层状态标记 |
| SELF_EVENT | 业务方自行维护上报状态 |

### 约定

- SPM 参数通过 `kSpmB` / `kSpmC` / `kSpmD` 常量传入；有 spmC 无 spmD 时自动补 0
- TrackNode 层级继承：`collectTrack()` 递归向上遍历 View 树，按根到叶顺序合并 SPM 参数
- preSpm 自动链式记录：TrackerManager 通过 `lastPvSpm` / `currentPreSpm` 维护；PV 的 preSpm 指向上一页面 spm，EXP/CLK 的 preSpm 指向当前页面 PV spm
- 所有埋点数据上报前自动经过 `PIIAnonymizer` 异步匿名化处理（支持 Key 规则和 Value 正则规则）
- 业务参数（user_id、device_sn 等）通过 `trackNode.addParams()` 手动注入，非自动注入

---

## 后端 (iot-service-unified)

### Local 与 Cloud 的区别

| | iot-service-local | iot-service-cloud |
|--|-------------------|-------------------|
| 包路径 | `com.addx.iotcamera.event.SnowPlowManager` | `com.addx.tracking.config.snowplow.SnowPlowManager` |
| 获取方式 | Spring 注入 `@Autowired` | 单例 `SnowPlowManager.getInstance()` |
| 事件标识 | iglu Schema URI（`iglu:com.iot_local/{event}/jsonschema/{version}`） | 直接用事件名字符串（`"bind_device_result"`） |
| 上下文 | 必须手动传 `getBaseMap(spm, type)` 作为第三个参数 | SDK 内部自动处理 |
| 条件上报 | 支持 `analyticWhenSwitchOn`（Redis 开关控制） | 不支持，直接上报 |
| 错误包装 | `SnowPlowManager.catchSnowPlowError(consumer)` | 业务方自行 try-catch |

### 注入位置

| 位置 | 说明 |
|------|------|
| Service 层（推荐） | 业务逻辑完成后上报，如 `BindService.analyticBindDeviceResult()` |
| Helper/Util 层 | 通用能力封装，如 `MsgHelper.analyticNotificationPush()` |
| 独立 Tracker 类 | 复杂模块可抽出，如 `AlexaSafemoSnowPlowTracker` |
| Controller 层（不推荐） | 仅用于测试/调试 |

### 约定

- 错误处理：**必须**用 `catchSnowPlowError` 包装（local）或 try-catch（cloud），不能让埋点异常影响主流程
- 敏感信息：MAC 地址等必须 SHA256 哈希后上报（`PhosUtils.sha256()`）
- 枚举值：使用 `.name()` 转为字符串上报
- 布尔结果：成功/失败用 `successFlag.equals(errorCode)` 转为 boolean
- 耗时记录：关键操作记录 `cost_time = System.currentTimeMillis() - startTime`
- 追踪 ID：通过 `TrackingThreadLocal.getTraceId()` 关联日志
- 命名约定：埋点方法以 `analytic` 开头（如 `analyticBindDeviceResult`）
- 参数格式：扁平化 `Map<String, Object>`，不嵌套复杂对象

### 异步批量上报

- 后端埋点默认异步批量发送，开发者无需关心发送时机
- 沙盒环境逐条发送（`batchSize=1`），便于实时调试验证
