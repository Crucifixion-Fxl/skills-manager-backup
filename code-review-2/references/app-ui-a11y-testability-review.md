# App UI 无障碍 + 可测试性审查

用于 code-review Step 2（内容质量审查），对 app 端新功能 UI 做**双轴静态卡点**：

- **可测试性轴（T）—— 硬卡**：新增交互控件是否带稳定的 UI 自动化定位标识（`accessibilityIdentifier` / Compose `testTag` / Flutter `Key`）。
- **无障碍轴（A）—— 试跑（全 ⚠️，不阻断）**：新增交互/图像元素是否带屏幕阅读器可读名、动态字体、热区、装饰排除等无障碍标注。

**核心思想**：和 i18n 资源审查同源 —— 不查运行时、不查测试报告，直接审查 diff 里落地的 UI 源码。"新功能没给无障碍/可测试性钩子"这种失败模式，在最终源码里一定有痕迹。

> **本检查查的是"标注存在性"，不是"标注质量"。** 它能挡住"纯图标按钮零 label / 零 identifier"这类机械可判的硬伤，但不验证朗读顺序、对比度、label 文案是否有意义、UITest 是否真能跑过 —— 那些需要运行时轴（VoiceOver/TalkBack 走查 + 真机自动化），不在本 reference 范围。定位是**抬高地板**。

---

## 适用现状与各端触达（2026-06 实测）

> **新业务以 Flutter 为主**，因此本卡点的真实约束力**集中在 Flutter**——这与新功能代码的落点一致，是有意的价值聚焦，不是覆盖缺陷。

| 端 | 栈 / UI 落点 | T 轴真实触达 | 说明 |
|:---|:---|:---|:---|
| **Flutter** | `.dart` widget | **强**（主目标） | 裸 `GestureDetector`/`IconButton`/`*Button` 无 `key`/`Semantics` 会真报 🔴。新业务都在这里 |
| Android | Java/XML（遗留） | 弱 | `android:id` 为视图绑定刚需、近乎遍地，新交互元素天然带定位锚，T1 极少触发（实测 80 MR 无命中）。作兜底 |
| iOS | UIKit + `.xib`/`.storyboard`（遗留） | 当前≈0 | UI 控件多在 Interface Builder 里，**本 reference glob 仅 `*.swift` 暂未覆盖 `.xib`/`.storyboard`**。新业务不走 iOS UIKit，故列为后续增强项（TODO：加 xib `<button>`/`<textField>` 的 identifier/accessibility 检测），不阻塞当前上线 |

实测依据：g0-flutter-module !411/!392 真实命中 T1 🔴（见文末附录）；g0-android 80 MR、g0-ios 80 MR 均无真实触发。

---

## 适用时机

仅当 diff 中**包含 app UI 源文件改动**时触发。无 UI 文件改动则跳过整节。

### 文件识别（glob）

| 端 | UI 源文件 glob | 备注 |
|:---|:---|:---|
| iOS | `**/*.swift` | UIKit `UIView`/`UIViewController` 子类、SwiftUI `View` |
| Android | `**/res/layout*/**/*.xml`、`**/*.kt` | XML 布局、Jetpack Compose `@Composable` |
| Flutter | `**/*.dart` | `Widget` / `build()` |

**路径排除（先于一切判定，整文件跳过）**：以下不是生产 UI，卡点对其开火即误报——

- `**/example/**`、`**/examples/**`、`**/demo/**`、`**/gallery/**`：SDK/包的示例 app、组件画廊（实测 g0-flutter-module 的 `packages/*/example/lib/main.dart` demo 按钮被误命中）
- 生成文件：`**/*.g.dart`、`**/*.freezed.dart`、`**/*.gen.dart`、`**/R.java`、`**/databinding/**`（无手写 UI 语义，标注本就不该手加）

**启发式过滤（强制，先做）**：`*.swift` / `*.kt` / `*.dart` 大量是纯逻辑文件（model、repository、service、ViewModel、util），不能见后缀就进维度。只有 diff 新增/修改的 hunk 里出现 UI 构件关键字才进本检查，否则跳过该文件：

| 端 | 入选关键字（任一命中即进） |
|:---|:---|
| iOS UIKit | `UIButton`、`UIImageView`、`UILabel`、`UITextField`、`UITextView`、`UISwitch`、`addTarget`、`isAccessibilityElement` |
| iOS SwiftUI | `Button`、`Image(`、`TextField`、`Toggle`、`.onTapGesture`、`NavigationLink`、`Label(` |
| Android XML | `<ImageButton`、`<ImageView`、`<Button`、`<EditText`、`<TextView`、`<Switch`、`android:onClick` |
| Android Compose | `IconButton`、`Icon(`、`Image(`、`Button(`、`TextField`、`Switch(`、`Modifier.clickable` |
| Flutter | `IconButton`、`Icon(`、`Image`、`InkWell`、`GestureDetector`、`TextField`、`ElevatedButton`、`Switch(` |

---

## 增量审查原则（强制）

**只审查 diff 中新增（`+`）或修改的 UI 元素**，不做全量历史扫描。理由同 i18n：
1. 历史遗留 UI 不在本次 MR 责任范围（app 端无障碍基线≈0，全量扫会淹没本次问题）
2. 控制 review 性能与 token

执行步骤：
1. 识别 diff 涉及的 UI 文件，按上面 glob + 关键字过滤分类
2. 从 diff 提取本次**净新增/修改的 UI 元素**：交互控件（button / 可点击容器 / 输入框 / 开关 / 列表项）、图像元素（图标 / 图片）
   - **排除"移动 / 重排"（强制，否则误报）**：布局重构会把旧控件挪位置或改缩进，在 diff 里**同一个控件同时出现在 `-` 和 `+` 段、内容等价**（相同 `onTap`/`onPressed`、相同 child/样式）——这是**搬运不是新增**，**不报**。判据：该控件在本文件 diff 里 `+` 出现数 ≈ `-` 出现数且块内容等价 → 视为移动，跳过。只有 `+` 无对应 `-`（净新增）的控件才进入判定。
   - 实例：customer-care !259 的 `ElevatedButton(onYes)`/`TextButton(onNo)` 在 sticky 布局重构里 +2/-2 等价搬运 → 不报；g0-flutter-module !411/!392 的 `GestureDetector` 是 +1/-0 净新增 → 进入 T1 判定。
3. **读整份 UI 文件**（不止 diff hunk）确认标注是否在同文件别处设置 —— CI（Opencode detached-HEAD）文件在盘上，可 Read。这一步消除"label/identifier 在本文件别处设"的误报
4. 对每个元素，按下面 T 组 + A 组维度逐项判断

> **跨文件兜底无法静态确认**：若标注可能在父类、扩展、共享组件（如 `a4x_ui_kit`）里设置，**降级处理**（T → ⚠️ 而非 🔴，A 本就 ⚠️），并备注"疑似在共享组件设置，请确认"。`a4x_ui_kit` 当前几乎不集中处理 a11y，缺标注多为真缺，但**正确修法常在共享组件而非调用处** —— finding 里要提示这一点。

---

## 可测试性轴（T）—— 硬卡（三端强制）

> **团队基线（实测，不要误判为"习惯已在"）**：可测试性标识的存量极不均，且"有标识 ≠ 为测试服务"——
> - iOS g0-ios：`accessibilityIdentifier` 347 处 / 179 文件（占 .swift 16%），但 XCUITest by-id 定位仅 22 次调用。**三端里相对最成熟，但远谈不上普遍。**
> - Android g0-android：Java/XML 老栈（1118 个 .java，0 个 @Composable）；`android:id` 1092 处但**多为视图绑定附带、非为测试刻意添加**，Espresso `withId` = 0。
> - Flutter g0-flutter-module / naturehood：`Key()/ValueKey()` 147 / 172 处，但**多为 widget 重建/状态保持用途**，`find.byKey` = 0。
>
> **所以 T 轴是"建习惯型门禁"，不是"守已有习惯"**：目的是从新功能开始，强制每个新交互控件可被 UI 自动化稳定定位。**老代码不受影响（增量审查），但新控件缺标识会被卡** —— 这是有意为之的摩擦，不是误报。各端摩擦预期不同（见下），reviewer 据此设预期，别因为"老代码也没有"就放行新代码。

### T1 — 交互控件缺稳定测试标识（🔴 高置信子集 / 其余 ⚠️）

**定义**：新增的交互控件（按钮 / 可点击容器 / 输入框 / 开关 / 列表项 cell）没有稳定的 UI 自动化定位标识。

**各端标识 API**：

| 端 | 测试标识 API | 说明 |
|:---|:---|:---|
| iOS | `.accessibilityIdentifier("...")`（UIKit）/ `.accessibilityIdentifier("...")` modifier（SwiftUI）| XCUITest 定位用，**与 `accessibilityLabel` 互不替代** |
| Android XML | `android:id="@+id/..."` 或 `android:tag` | Espresso `withId` / UIAutomator |
| Android Compose | `Modifier.testTag("...")`，且 Activity/Theme 处设 `Modifier.semantics { testTagsAsResourceId = true }`（否则 UIAutomator 取不到）| 缺 `testTagsAsResourceId` 全局开关 → 即便有 testTag 仍取不到 |
| Flutter | `key: const Key('...')` / `ValueKey('...')`，或 `Semantics(identifier: '...')` | `find.byKey` / `find.bySemanticsIdentifier` |

**🔴 高置信判定（计入红线）—— 全部满足才打 🔴**：
- 新增的是**关键交互控件**（用户主路径上的按钮、提交/确认、输入框、主要 CTA、可点击列表项），且
- 整份文件内该控件**完全没有**任何测试标识，且
- 不是从已带标识的共享组件直接复用（疑似复用 → 降 ⚠️）

**⚠️ 判定（不阻断）**：次要交互元素缺标识、或高置信三条任一不确定。

**典型失败**：

```kotlin
// Compose：新增主按钮无 testTag → UITest 只能靠文案定位，本地化后必 flaky
Button(onClick = { submit() }) { Text(stringResource(R.string.submit)) }   // 🔴 缺 testTag
```

```swift
// SwiftUI：新增图标按钮无 identifier
Button(action: share) { Image(systemName: "square.and.arrow.up") }   // 🔴 缺 .accessibilityIdentifier
```

**各端摩擦预期与判定要点（实测基线决定，reviewer 必读）**：

| 端 | T1 满足条件 | 摩擦 | 说明 |
|:---|:---|:---|:---|
| Android XML | 新增交互 view 有 `android:id`（即 Espresso `withId` 的定位锚） | **低** | `android:id` 是 Espresso 的定位符，绑定本就常带 id，多数新控件天然满足。只在新增**可点击容器/自定义交互 view 没有 id** 时才 🔴 —— 低频但有效（确保新控件可寻址）。**不要**因为"团队没写 Espresso 测试"就放宽要求，门禁要的是"可被定位"而非"已被测试" |
| iOS | 新增关键交互控件有 `.accessibilityIdentifier` | **中** | 16% 文件已有，习惯部分在；新控件需显式补 |
| Compose / Flutter | Compose `testTag`(+全局 `testTagsAsResourceId`) / Flutter `Key`·`Semantics(identifier:)` | **高** | 现存 `Key` 多为 reconciliation 非测试用，开发通常不给按钮加 testTag/Key。这一档摩擦最大，是"建习惯"的主战场；🔴 仍只打高置信（关键交互控件零标识），次要元素 ⚠️，避免初期过度拦截 |

> Compose 特别注意：只有 `Modifier.testTag(...)` 而**全局没设** `testTagsAsResourceId = true`，UIAutomator/Espresso 仍取不到 → 视为 T1 未满足（⚠️，因为可能在主题/Activity 别处设了全局开关，读不到时降级备注）。

#### T1 不报 / 降级清单（Flutter 主战场必读，压误报）

Flutter 里 `GestureDetector`/`InkWell` 大量用于**非按钮**用途。以下情形**不报 T1**（或降 ℹ️），避免在主战场乱报砸 gate 信任：

- **背景层手势**：点空白收键盘 / 关弹层（如 `onTap: () => FocusScope.of(context).unfocus()`、点遮罩 dismiss）
- **纯滑动/拖拽手势**：只有 `onHorizontalDragUpdate`/`onPanUpdate`/`onVerticalDrag*` 等、**无 `onTap`** 的手势识别
- **外层已有定位锚**：父容器已带 `key`，内部子元素的包装无需各自再加（定位锚在父级即可）
- **装饰性/整块占位点击且无独立业务语义** → 疑似时降 ⚠️ 备注，不 🔴

判 🔴 的前提仍是 **承载独立业务语义的交互入口**：有 `onTap` + 业务动作（跳转 / 提交 / 埋点 / 状态变更），如按钮、CTA、可点击列表项、可点击业务卡片。**任一不确定 → 降 ⚠️，不打红线。**

### T2 — 测试标识不稳定（⚠️）

**定义**：测试标识用了**跨运行不确定**的值，导致 UITest 定位 flaky。

命中模式（⚠️）：
- 用**随机值 / UUID / 时间戳**当 id（每次运行都变，`testTag(uuid)` / `testTag(timestamp)`）
- 用**本地化文案**当 id（`accessibilityIdentifier(localizedTitle)` → 换语言即失效）

→ ⚠️ 提示改用稳定常量。

> **不报**：list 用 `index` 拼标识（`testTag("item_$index")` / `ValueKey(index)`）是**确定性的、数据 list 的基本写法，不报 T2**，也满足 T1 的"有定位锚"。仅当测试需要按"**身份**"而非"**位置**"定位某项时，建议（ℹ️ 非 ⚠️、不阻断）改用业务 id（`ValueKey(item.id)`）。
> 注：`ValueKey(index)` 用作 Flutter **reconciliation key**（非测试）在可重排列表里是状态错乱反模式——但那属常规 code review 的正确性问题，**不在本维度**。

---

## 无障碍轴（A）—— 试跑（全部 ⚠️，不阻断、不进红线）

> 阶段一：A 组全部 ⚠️，只在 §3 输出子表收集数据 + 推动习惯，**不计入 Step 4 红线**。
> 阶段二升级开关见文末「阶段二」。
> 现状：朗读用 `accessibilityLabel` 全仓仅 1 个文件 —— 几乎零基线，先观察后收紧。

### A1 — 交互/图像元素缺屏幕阅读器可读名（⚠️；阶段二高置信 → 🔴）

**定义**：纯图标按钮、图片按钮、可点击图像、承载语义的图标，没有可读名，阅读器会读"按钮"或静默。

**各端可读名 API**：

| 端 | 可读名 API |
|:---|:---|
| iOS UIKit | `accessibilityLabel`（图标 `UIButton` 无 title 时必设）；`UIImageView` 若 `isAccessibilityElement=true` 需 label |
| iOS SwiftUI | `.accessibilityLabel("...")`；`Image(systemName:)` 当按钮内容时需 label 或外层 `.accessibilityLabel` |
| Android XML | `android:contentDescription`（`ImageButton`/`ImageView`）|
| Android Compose | `Icon(..., contentDescription = "...")` / `Image(..., contentDescription = "...")`；交互图标 `contentDescription` 非空 |
| Flutter | `IconButton(tooltip: "...")` / `Image(semanticLabel: "...")` / 外层 `Semantics(label: "...")` |

**判定**：交互/语义图像同元素内有可见文本（如带文字的按钮）→ 不报（文本即隐式可读名）。仅在无任何可见文本兜底时报 ⚠️。装饰性疑似 → 归 A2。

**典型失败**：

```xml
<!-- Android：图标按钮无 contentDescription -->
<ImageButton android:id="@+id/btn_share" android:src="@drawable/ic_share" />   <!-- ⚠️ 缺 contentDescription -->
```

### A2 — 装饰性元素未显式排除（⚠️）

纯装饰图未显式标记从无障碍树排除 → 阅读器读出无意义噪音。
- iOS：未用 `Image(decorative:)` / `.accessibilityHidden(true)`
- Android：未设 `android:importantForAccessibility="no"` 或 `contentDescription="@null"`
- Flutter：未用 `ExcludeSemantics` / 未显式 `semanticLabel`

> 静态无法确证"是否装饰"，永远 ⚠️ + 备注，绝不 🔴。

### A3 — 字号不可缩放 / 强制关闭动态字体（⚠️）

- iOS：固定 `.font(.system(size: 14))` 未走 Dynamic Type text style；`UILabel.adjustsFontForContentSizeCategory = false`
- Android：`textSize` 用 `dp`/`px` 而非 `sp`
- Flutter：`Text` 固定 `fontSize` 处于固定高度容器易截断；显式 `textScaler: TextScaler.noScaling` / `textScaleFactor: 1.0` 关闭缩放

> **强制关闭缩放**（如显式 `textScaleFactor: 1.0`）危害更大，阶段一仍 ⚠️ 但 finding 标注"高危：显式禁用了动态字体"，作为阶段二候选红线。

### A4 — 点击热区过小（⚠️）

显式尺寸 < 44pt(iOS) / 48dp(Android) / `kMinInteractiveDimension`=48(Flutter) 的可点击元素。
> 真实渲染尺寸取决于约束/padding/父容器，静态算不准 → 永远 ⚠️。

### A5 — 输入控件缺标签关联（⚠️；阶段二高置信 → 🔴）

- Android XML：`EditText` 无 `android:hint` 且无 `labelFor` 指向它
- iOS：`UITextField`/SwiftUI `TextField` 无 `accessibilityLabel` 且无可见标签
- Flutter：`TextField` 无 `InputDecoration(labelText/hintText)` 且无外层 `Semantics`

---

## 输出格式

在 code-review 5 段输出的 **§3 内容质量** 中追加子节：

```markdown
**app UI 无障碍 + 可测试性**（基于 diff 增量审查）

涉及文件：`<file>` × N（iOS/Android/Flutter）

可测试性轴（T，硬卡）：
| 维度 | 严重级 | 文件:行 | 元素 | 问题描述 |
|:---|:---|:---|:---|:---|
| T1 测试标识 | 🔴 P0 | LoginScreen.kt:42 | submit Button | 主按钮无 testTag，UITest 仅能靠本地化文案定位 |
| T2 标识不稳定 | ⚠️ P1 | FeedList.kt:88 | item cell | testTag 用本地化标题拼接 |

无障碍轴（A，试跑/不阻断）：
| 维度 | 严重级 | 文件:行 | 元素 | 问题描述 |
|:---|:---|:---|:---|:---|
| A1 可读名 | ⚠️ | ShareBar.swift:30 | share Button | 图标按钮无 accessibilityLabel |
| A5 输入标签 | ⚠️ | LoginScreen.kt:55 | EditText | 无 hint / labelFor |
```

T、A 两轴均无问题时，仅输出一行：
> **app UI 无障碍/可测试性**：本次涉及 N 个 UI 文件，T 轴硬卡通过 ✅，A 轴无 ⚠️。

---

## 与 Step 4 红线的关联

**只有 T 轴的高置信项进红线**（结论必须为"不通过"）：

- **T1 高置信**：新增关键交互控件完全无稳定测试标识 —— UI 自动化无法稳定定位，等同已实现功能但不可测。

**A 轴阶段一全部不进红线**（记入"建议改进"）。T2 记入"建议改进"。

---

## 阶段二（升级开关，默认关闭）

当 A 轴试跑期（建议 2–3 周）证明误报率可接受后，把以下**高置信子集**提升为 🔴 进红线（仅改本节判定 + SKILL.md Step 4 红线表对应条目，无需改维度结构）：

- **A1 高置信**：纯图标/图片按钮等交互元素，整份文件内完全无任何可读名且无可见文本兜底
- **A5 高置信**：输入控件完全无标签关联且无可见标签
- （可选）**A3 强制关闭动态字体**：显式 `textScaleFactor: 1.0` / `adjustsFontForContentSizeCategory = false` 关闭缩放

升级时同步更新 SKILL.md §4 红线表与本 reference 头部的轴定位说明。

---

## LLM 执行提示

执行此检查的 sub-agent 应：

1. **先列 diff 涉及的 UI 文件清单**（过启发式关键字）—— 没有就跳过整节
2. **读整份相关 UI 文件**（不止 hunk），确认标注是否在同文件别处设置，消除跨行误报
3. **逐元素、先 T 轴后 A 轴**过维度，不要批量泛泛判断
4. **每个发现给出 file:line + 元素 + 源码片段**，便于直接定位
5. **T1 红线高精确**：三条件全满足才 🔴；任一不确定（疑似共享组件复用、疑似次要元素）降 ⚠️ 备注
6. **A 轴一律 ⚠️**（阶段一），判断装饰性/热区/语种倾向保守、宁漏报不误报，备注假设
7. **不混淆两轴**：`accessibilityIdentifier`（测试，T 轴）≠ `accessibilityLabel`（朗读，A 轴）；有 identifier 没 label = A1，有 label 没 identifier = T1，各报各轴
8. **修复位置提示**：缺标注若应在共享组件（`a4x_ui_kit` 等）统一补，finding 注明，避免作者只在调用处打补丁

---

## 附录：真实触发示例（Flutter，来自 g0-flutter-module 历史 MR）

> 以下是用本 reference 规则对真实 MR diff 判定的结果，作为 reviewer 校准 🔴 判定的参照。Android/iOS 因栈特性（id 遍地 / UI 在 IB）在历史 MR 中无真实触发，对应的判定写法见上文各维度的合成示例。

### 示例 1 — !411 free-license 领取页（CMS 文案驱动的转化入口）

```dart
// lib/pages/vip/free_license/view.dart
GestureDetector(
  onTap: () {
    trackLearnMoreClick();
    Navigator.of(context).push(MaterialPageRoute(builder: (_) => AdSupportedAboutPage(...)));
  },
  child: Padding(padding: ..., child: Text(ad.learnMoreLabel, ...)),   // 文案来自 CMS
)
```
**判定：T1 🔴** —— 承载语义的交互入口（独立 onTap + 埋点 + 跳转），无 `key`/`Semantics`。文案 `ad.learnMoreLabel` 是 CMS 动态值，UI 自动化连"靠文案定位"都做不到。修复：`key: const ValueKey('free_license_learn_more')`。
（同 MR `about_page/view.dart` 的返回 `IconButton` → T1 ⚠️ 次要导航 + A1 ⚠️ 图标按钮无 tooltip。）

### 示例 2 — !392 添加设备页主按钮

```dart
// lib/pages/bind/add_device/widgets.dart
GestureDetector(
  onTap: onTap,
  behavior: HitTestBehavior.opaque,
  child: Container(height: 50, /* primary 色, 圆角 100 */ ...),   // 主按钮样式
)
```
**判定：T1 🔴** —— 主按钮样式的可点击区，无 `key`/`Semantics` → UI 自动化无稳定定位锚。修复：补 `key`。

### 对照：不触发的真实案例

g0-android !1212「Ad Free License cancel UI」：UI 经共享组件 `CommonCornerDialog` + 数据字段（`cloudServiceButtonType`）驱动，diff 无裸交互控件且 Android 控件天然带 `android:id` → **T 轴放行**。说明卡点不会对"用了组件封装/已有定位锚"的改动误伤。
