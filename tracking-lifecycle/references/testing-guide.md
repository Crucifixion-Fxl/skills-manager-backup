# 埋点测试指南

各端埋点的分层测试方案。按 [verification-strategy.md](verification-strategy.md) 的分层策略，
本文档覆盖 **L1（mock SDK）** 和 **L2（test sink）** 两层的具体实现。

L3（沙盒验证）和发布前确认（staging）的流程参见 SKILL.md 对应 Step。

---

# L1 — 单元测试（mock SDK）

目标：在单测中 **verify SDK 调用参数正确**，不发送真实网络请求，不渲染 UI。

## Android — MockK

```kotlin
@ExtendWith(MockKExtension::class)
class MyFeatureTest {

    @MockK(relaxed = true)
    lateinit var mockTrackHandler: TrackHandler

    @Before
    fun setUp() {
        // 方式 1: Mock TrackHandler 接口（推荐）
        mockkStatic("com.a4x.tracker_core.TrackerKt")
        every { any<View>().postTrack(any(), any()) } just Runs

        // 方式 2: Mock 专用 Tracker 单例
        mockkObject(PaymentTrack.Companion)
        val mockTrack: PaymentTrack = mockk(relaxed = true)
        every { PaymentTrack.get() } returns mockTrack
    }

    @Test
    fun should_trackClickEvent_when_buyButtonClicked() {
        viewModel.onBuyClicked()

        verify(exactly = 1) {
            mockTrack.reportClick("buy_btn", match {
                it["product_id"] == "12345"
            })
        }
    }

    @After
    fun tearDown() {
        unmockkAll()
    }
}
```

**入口清单**：
- UI 事件: `View.postTrack(SpmEventType, extraParams)`
- 自定义事件: `postSelfEventTrack(eventKey, params)`
- 通用事件: `EventTracker.shared.track(eventKey, params)`

## iOS — Protocol Mock

```swift
class MockTracker: TrackerInterface {
    var spmA: String = "test"
    var nameSpace: String = "test"
    var endpointUrl: String = "https://test"
    var jsonSchemaVersion: String = "1-0-0"

    var capturedEvents: [(spm: String, type: SpmEventType, data: [String: Any])] = []

    func trackSpmEvent(spm: String, preSpm: String, spmType: SpmEventType, data: [String: Any]) {
        capturedEvents.append((spm, spmType, data))
    }

    func trackNonSpmEvent(eventName: String, data: [String: Any]) {
        capturedEvents.append((eventName, .self_event, data))
    }

    func updateTracker(trackUrl url: String, trackNameSpace nameSpace: String) {}
}

class MyFeatureTests: XCTestCase {
    var mockTracker: MockTracker!

    override func setUp() {
        mockTracker = MockTracker()
        TrackerManager.shared.initTrack(tracker: mockTracker)
    }

    func test_trackPV_when_pageAppears() {
        sut.viewDidLoad()

        XCTAssertEqual(mockTracker.capturedEvents.count, 1)
        XCTAssertEqual(mockTracker.capturedEvents[0].type, .pv)
        XCTAssertEqual(mockTracker.capturedEvents[0].spm, "store_page")
    }
}
```

**入口清单**：
- SPM 事件: `TrackerManager.shared.trackSpmEvent(...)`
- 非 SPM 事件: `TrackerManager.shared.trackNonSpmEvent(...)`
- 便捷方法: `Tracker.trackEvent(view:spmType:params:)`、`Tracker.track(eventKey:params:)`

## Flutter — Platform Channel Stub

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:event_tracker/event_tracker.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      const MethodChannel('flutter_boost_channel'),
      (MethodCall methodCall) async {
        if (methodCall.method == 'track') {
          return null;
        }
        return null;
      },
    );
  });

  test('should send PV event on page load', () async {
    await tester.pumpWidget(StorePage());
    // 验证 channel 被调用（通过 mock handler 中的捕获逻辑）
  });
}
```

**入口清单**：
- 所有事件: `NormalTracker.track(Event event, {String? preSpm})`
- 底层通道: `TrackerChannel.track({required Map<String, dynamic> data})`

## 后端 Java — Mockito

```java
@ExtendWith(MockitoExtension.class)
class MyServiceTest {

    @Mock
    private SnowPlowManager snowPlowManager;

    @InjectMocks
    private DeviceBindService deviceBindService;

    @Test
    void should_trackBindEvent_when_deviceBound() {
        try (MockedStatic<SnowPlowManager> mocked = Mockito.mockStatic(SnowPlowManager.class)) {
            mocked.when(SnowPlowManager::getInstance).thenReturn(snowPlowManager);

            deviceBindService.bindDevice("ABC123", "U456");

            verify(snowPlowManager).analyticSelfEvent(
                eq("device_bind"),
                argThat(params ->
                    "ABC123".equals(params.get("device_id")) &&
                    "U456".equals(params.get("user_id"))
                )
            );
        }
    }

    @Test
    void should_handleTrackingFailure_gracefully() {
        try (MockedStatic<SnowPlowManager> mocked = Mockito.mockStatic(SnowPlowManager.class)) {
            mocked.when(SnowPlowManager::getInstance).thenReturn(snowPlowManager);
            doThrow(new RuntimeException("network error"))
                .when(snowPlowManager).analyticSelfEvent(any(), any());

            assertDoesNotThrow(() -> deviceBindService.bindDevice("ABC123", "U456"));
        }
    }
}
```

**入口清单**：
- 通用事件: `SnowPlowManager.getInstance().analyticSelfEvent(eventName, params)`
- 底层 API: `SnowPlowManager.getInstance().analytic(schema, data, context)`
- 安全包装: `SnowPlowManager.catchSnowPlowError(consumer)` — 吞异常，仅日志

---

# L2 — 集成测试（test sink）

目标：注入 test sink 替换真实 reporter，**捕获完整事件列表**后对事件名、payload、次数、顺序做结构化断言。

L2 与 L1 的区别：L1 verify 的是 "SDK 被调用了"，L2 assert 的是 "产出了什么事件数据"。

## Flutter — TestTrackSink + Widget Test

Flutter 的 `NormalTracker.track()` 最终通过 `TrackerChannel` 发送数据。
L2 的关键是在 `TrackerChannel` 层注入一个 test sink，拦截所有事件。

### TestTrackSink 实现

```dart
/// 测试用 sink，拦截所有埋点事件供断言
class TestTrackSink {
  final List<TrackEvent> events = [];

  void capture(TrackEvent event) {
    events.add(event);
  }

  void reset() => events.clear();

  /// 按事件名过滤
  List<TrackEvent> byName(String name) =>
      events.where((e) => e.name == name).toList();

  /// 按类型过滤
  List<TrackEvent> byType(String type) =>
      events.where((e) => e.type == type).toList();
}

/// 捕获到的事件结构
class TrackEvent {
  final String name;
  final String type; // pv, clk, exp, self_event
  final String? spm;
  final Map<String, dynamic> data;
  final DateTime timestamp;

  TrackEvent({
    required this.name,
    required this.type,
    this.spm,
    required this.data,
    DateTime? timestamp,
  }) : timestamp = timestamp ?? DateTime.now();
}
```

### Widget Test 完整示例

```dart
import 'package:flutter_test/flutter_test.dart';

void main() {
  late TestTrackSink sink;

  setUp(() {
    sink = TestTrackSink();
    // 注入 test sink：拦截 Platform Channel 调用并捕获事件
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      const MethodChannel('flutter_boost_channel'),
      (MethodCall methodCall) async {
        if (methodCall.method == 'track') {
          final args = methodCall.arguments as Map;
          sink.capture(TrackEvent(
            name: args['point'] ?? args['event'] ?? '',
            type: args['type'] ?? 'unknown',
            spm: args['spm'],
            data: Map<String, dynamic>.from(args['data'] ?? {}),
          ));
        }
        return null;
      },
    );
  });

  tearDown(() => sink.reset());

  group('商店页埋点', () {
    testWidgets('进入商店页应触发 PV 事件', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: StorePage()));
      await tester.pumpAndSettle();

      // 断言：事件名
      expect(sink.events.length, equals(1));
      expect(sink.events[0].name, equals('store_page'));
      expect(sink.events[0].type, equals('pv'));
    });

    testWidgets('点击购买按钮应触发 CLK 事件且参数正确', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: StorePage()));
      await tester.pumpAndSettle();
      sink.reset(); // 清除 PV 事件

      await tester.tap(find.byKey(const Key('buy_button')));
      await tester.pumpAndSettle();

      // 断言：事件名 + payload 字段 + 值
      final clkEvents = sink.byType('clk');
      expect(clkEvents.length, equals(1));
      expect(clkEvents[0].name, equals('buy_btn'));
      expect(clkEvents[0].data['product_id'], equals('SKU_12345'));
      expect(clkEvents[0].data['price'], equals(29.99));
    });

    testWidgets('商品曝光只应触发一次', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: StorePage()));
      await tester.pumpAndSettle();

      // 模拟滚动触发曝光
      await tester.drag(find.byType(ListView), const Offset(0, -300));
      await tester.pumpAndSettle();
      // 再次滚动回来
      await tester.drag(find.byType(ListView), const Offset(0, 300));
      await tester.pumpAndSettle();

      // 断言：触发次数（去重后应为 1）
      final expEvents = sink.byType('exp');
      expect(expEvents.where((e) => e.name == 'product_card').length, equals(1));
    });

    testWidgets('页面浏览 → 曝光 → 点击的事件顺序正确', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: StorePage()));
      await tester.pumpAndSettle();

      await tester.drag(find.byType(ListView), const Offset(0, -300));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('buy_button')));
      await tester.pumpAndSettle();

      // 断言：事件顺序
      final names = sink.events.map((e) => e.name).toList();
      expect(names, equals(['store_page', 'product_card', 'buy_btn']));
    });
  });
}
```

### 断言速查

| 断言对象 | 写法 |
|---------|------|
| 事件名 | `expect(sink.events[0].name, equals('store_page'))` |
| payload 字段 | `expect(sink.events[0].data['product_id'], equals('SKU_12345'))` |
| 触发次数 | `expect(sink.byName('buy_btn').length, equals(1))` |
| 事件顺序 | `expect(sink.events.map((e) => e.name).toList(), equals([...]))` |
| 不应触发 | `expect(sink.byName('error_event'), isEmpty)` |

## Android — TestTrackCollector

Android 的 L2 方案基于 `TrackHandler` 接口注入 test collector。

```kotlin
class TestTrackCollector : TrackHandler {
    data class CapturedEvent(
        val name: String,
        val type: SpmEventType,
        val params: Map<String, Any?>,
    )

    val events = mutableListOf<CapturedEvent>()

    override fun trackSpmEvent(spm: String, type: SpmEventType, params: Map<String, Any?>) {
        events.add(CapturedEvent(spm, type, params))
    }

    fun reset() = events.clear()
    fun byName(name: String) = events.filter { it.name == name }
    fun byType(type: SpmEventType) = events.filter { it.type == type }
}

// 在 Instrumented Test 中使用
@RunWith(AndroidJUnit4::class)
class StorePageTrackingTest {

    private val collector = TestTrackCollector()

    @Before
    fun setUp() {
        // 注入 test collector 替换真实 TrackHandler
        TrackerCore.setHandler(collector)
    }

    @Test
    fun should_trackPV_and_CLK_in_correct_order() {
        // 启动页面
        val scenario = launchActivity<StoreActivity>()

        // 点击购买按钮
        onView(withId(R.id.buy_button)).perform(click())

        // 断言事件顺序
        assertThat(collector.events.map { it.name })
            .isEqualTo(listOf("store_page", "buy_btn"))

        // 断言 CLK 参数
        val clkEvent = collector.byType(SpmEventType.CLK).first()
        assertThat(clkEvent.params["product_id"]).isEqualTo("12345")
    }
}
```

## iOS — MockTracker（已有，天然支持 L2）

iOS 的 `MockTracker`（见 L1 部分）通过 `capturedEvents` 数组捕获事件，已经是 test sink 模式。
直接用于 UI Test 即可：

```swift
func test_eventSequence_pageView_then_click() {
    // 触发页面加载
    sut.viewDidLoad()
    // 触发点击
    sut.didTapBuyButton()

    // 断言顺序
    XCTAssertEqual(mockTracker.capturedEvents.map { $0.spm },
                   ["store_page", "buy_btn"])

    // 断言 CLK 参数
    let clkEvent = mockTracker.capturedEvents.last!
    XCTAssertEqual(clkEvent.type, .clk)
    XCTAssertEqual(clkEvent.data["product_id"] as? String, "SKU_12345")
}
```

## 后端 — 不适用

后端只有 SELF_DEFINE 事件，无 UI 交互链路。L1 的 Mockito verify 已足够验证调用逻辑。
如需验证上报格式，直接进入 L3 沙盒验证。
