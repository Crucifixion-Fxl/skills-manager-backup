# 公司 SDK API 签名

YAML 中的 `point` 值对应 SDK 调用中的事件标识参数。

## Flutter (g0-flutter-module)
```dart
import 'package:event_tracker/event_tracker.dart';

// 页面浏览 (PV) — page 参数传入 PAGE 级 point 值
NormalTracker.track(Event.pv(page: 'store_page', data: {'source': 'home_banner'}));

// 点击事件 (CLK) — page/module/component 对应 SPM 层级的 point 值
NormalTracker.track(Event.clk(page: 'store_page', module: 'product_detail', component: 'buy_btn', data: {'product_id': '12345'}));

// 自定义事件 — event 参数传入 SELF_DEFINE 级 point 值
NormalTracker.track(Event.custom(event: 'app_launch', data: {'source': 'push'}));
```

注意：
- `NormalTracker` 是通用场景，特定模块有专用 Tracker（如 `BindTracker`、`VipTracker`）
- CLK 事件通过 page/module/component 参数自动组装 SPM，无需手动拼接
- 无独立的 `trackExposure` 方法，曝光事件需通过 `VisibilityDetector` + `Event.pv` 或自定义实现

### 完整方法签名
```dart
// NormalTracker — 所有事件统一入口
static Future<void> track(Event event, {String? preSpm}) async

// Event 工厂方法
Event.pv({required String page, Map<String, dynamic>? data})
Event.clk({required String page, required String module, required String component, Map<String, dynamic>? data})
Event.custom({required String event, Map<String, dynamic>? data})

// TrackerChannel — 底层 Platform Channel（通常不直接调用）
static Future<void> track({required Map<String, dynamic> data, String component = ""}) async
```

## Android 原生 (g0-android)
```kotlin
import com.a4x.tracker_core.postTrack
import com.a4x.tracker_core.postSelfEventTrack
import com.a4x.tracker_core.SpmEventType

// 页面浏览 (PV) — 在 Activity 中调用
activity.postTrack(SpmEventType.PV)

// 点击事件 (CLK) — 在 View 上调用，extraParams 传入业务参数
view.postTrack(SpmEventType.CLK, extraParams = mapOf("product_id" to "12345"))

// 自定义事件 — eventKey 传入 SELF_DEFINE 级 point 值
postSelfEventTrack("app_launch", hashMapOf("source" to "push"))
```

注意：
- SPM 信息通过注解（`@SpmPage`、`@SpmModule`）自动绑定到 Activity/View，无需手动传 point
- `SpmEventType` 枚举：`PV`、`CLK`、`EXP`、`SELF_EVENT`
- 特定模块有专用 Tracker（如 `BindTrack.reportClick`、`BindTrack.reportPage`）
- 通用事件使用 `EventTracker.shared.track(eventKey, params)` （`com.addx.common.EventTracker`）

### 完整方法签名
```kotlin
// 初始化
fun Application.initTracker(handler: TrackHandler)

// UI 事件 — SPM 自动从 View/Activity 继承
fun View.postTrack(spmType: SpmEventType, extraParams: Map<String, Any?> = emptyMap(), vararg classes: Class<*>)
fun Activity.postTrack(spmType: SpmEventType, vararg classes: Class<*>)  // 通用（CLK/EXP 等）
fun Activity.postTrack(spmB: String, vararg params: Pair<String, Any?>)  // PV 专用，自动设置 spmB + 触发 PV
fun Fragment.postTrack(spmType: SpmEventType, vararg classes: Class<*>)

// 自定义事件
fun postSelfEventTrack(eventKey: String, params: HashMap<String, Any?>)

// 动态更新 collector URL（沙盒调试用）
fun updateTrackUrl(urlEndpoint: String, namespace: String)
fun getTrackerUrl(): String?
```

### TrackHandler 接口（Mock 入口）
```kotlin
interface TrackHandler {
    fun onEvent(context: Context, eventType: SpmEventType, eventKey: String? = "", params: Map<String, Any?>)
    fun trackUIEvent(spm: String, preSpm: String, spmEventType: SpmEventType, params: Map<String, Any?>)
    fun trackSelfEvent(eventKey: String, params: Map<String, Any?>)
    fun updateTrackUrl(urlEndpoint: String, namespace: String)
    val trackerUrl: String
    val currentSpmB: String?
}
```

### SnowplowTrackHandler 配置项
```kotlin
SnowplowTrackHandler(
    context: Context,
    urlEndpoint: String,       // Collector URL
    namespace: String,         // Snowplow namespace
    spmA: String,              // 应用级 SPM（如 "smart_camera"）
    jsonSchemaVersion: String  // Schema 版本（如 "1-0-43"）
)
// Session 超时: 30 分钟
// base64 编码: false
// 自动追踪: sessionContext=true, platformContext=true, screenContext=true, applicationContext=true
```

## iOS 原生 (g0-ios)
```swift
import TrackerCore

// 页面浏览 (PV) — 在 viewDidLoad 中调用，spmB 传入 PAGE 级 point 值
self.track(spmB: "store_page")
self.track(spmB: "store_page", params: ["source": "home_banner"])

// 曝光事件 (EXP) — View 初始化完成后调用
self.track(spmType: .exp, params: [
    kSpmB: "store_page",
    kSpmC: "product_detail",
    "product_id": "SKU_12345"
])

// 点击事件 (CLK) — 按钮点击回调中调用
self.track(spmType: .clk, params: [
    kSpmB: "store_page",
    kSpmC: "product_detail",
    kSpmD: "buy_btn",
    "product_id": "SKU_12345"
])

// 自定义事件 — eventKey 传入 SELF_DEFINE 级 point 值
Tracker.track(eventKey: "app_launch", params: ["source": "push"])
TrackerManager.shared.trackNonSpmEvent(eventName: "bind_event", data: params)
```

注意：
- `SpmEventType` 枚举：`.pv`、`.exp`、`.clk`、`.self_event`
- SPM 参数通过 `kSpmB`、`kSpmC`、`kSpmD` 常量传入
- 支持 `TrackNode` 关联对象绑定，子 View 自动继承父 View 的 SPM
- 所有埋点数据自动通过 `PIIAnonymizer` 匿名化处理
- `BaseContext` 自动添加 user_id、device_sn、language 等基础参数

### 完整方法签名
```swift
// Tracker — 便捷方法
public static func trackEvent(view: UIView, spmType: SpmEventType, params: [String: Any]? = nil)
public static func trackEvent(viewController: UIViewController, spmType: SpmEventType, params: [String: Any]? = nil)
public static func track(eventKey: String, params: [String: Any]? = nil)
public static func addTrackParams(view: UIView, params: [String: Any])
public static func setReferrerTrackNode(viewController: UIViewController, view: UIView)

// TrackerManager — 底层 API
public func initTrack(tracker: TrackerInterface)
public func trackSpmEvent(spm: String, preSpm: String, spmType: SpmEventType, data: [String: Any])
public func trackNonSpmEvent(eventName: String, data: [String: Any])
public func updateTracker(trackUrl url: String, trackNameSpace nameSpace: String)
```

### TrackerInterface 协议（Mock 入口）
```swift
public protocol TrackerInterface {
    var spmA: String { get }
    var nameSpace: String { get }
    var endpointUrl: String { get }
    var jsonSchemaVersion: String { get }
    func updateTracker(trackUrl url: String, trackNameSpace nameSpace: String)
    func trackSpmEvent(spm: String, preSpm: String, spmType: SpmEventType, data: [String: Any])
    func trackNonSpmEvent(eventName: String, data: [String: Any])
}
```

### 常量
```swift
let spmA = "smart_camera"
let jsonSchemaVersion = "1-0-43"
let staingTrackerUrl = "https://us-test-log.theunismart.com"
let prodTrackerUrl = "https://log-us.kiwibit.com"
```

## 后端 (iot-service-unified)
```java
import com.addx.tracking.config.snowplow.SnowPlowManager;

// 服务端事件 — eventName 传入 SELF_DEFINE 级 point 值，params 为 Map<String, Object>
SnowPlowManager.getInstance().analyticSelfEvent("device_bind", Map.of("device_id", "ABC123", "user_id", "U456"));
```

注意：
- 后端只有 `analyticSelfEvent` 一种方式，所有事件都是 SELF_DEFINE 类型
- 也支持传入自定义实体对象作为第二个参数（如 `AlexaSnowPlowParamEntity`）
- 内部模块（iot-service-local）使用 `com.addx.iotcamera.event.SnowPlowManager`，API 类似

### 完整方法签名
```java
// === iot-service-cloud（外部 tracking SDK） ===
// 自定义事件（推荐入口）
public void analyticSelfEvent(String eventName, Map<String, Object> params)

// === iot-service-local（本地模块） ===
// 初始化/重建 Tracker
public void reBuildTracker(String url, String nameSpace, int type)
// type: INIT=0, APP_DEBUG=1, STOP_APP_DEBUG=2

// 底层 API — 传入自定义 Schema + Base Context
public void analytic(String schema, Map<String, Object> data, Map<String, Object> context)

// 获取基础 Context Map（含 sn, hub_version, spm, type, version, gitsha, log_trace_id）
public Map<String, Object> getBaseMap(String spm, String type)

// 安全包装 — 吞异常仅日志，不影响业务
public static void catchSnowPlowError(Consumer<SnowPlowManager> consumer)
```

### 配置项
```java
DEFAULT_BATCH_SIZE = 10    // 生产环境批量发送
batchSize = 1              // 沙盒/调试模式逐条发送
BASE_SCHEMA = "iglu:com.iot_local/base-schema/jsonschema/1-0-0"
DEFAULT_NAMESPACE = "camera_state_change"
// 自动 flush 间隔: 30 秒（ScheduledThreadPoolExecutor）
// 网络超时: 通过 SnowplowConfig Bean 配置
```
