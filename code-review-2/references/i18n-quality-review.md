# i18n 资源文件质量审查

用于 code-review Step 2（内容质量审查）中，对最终落地到代码仓库的 l10n 资源文件做质量校验。

**核心思想**：不查 Crowdin、不查代码引用，直接审查 App 真正要读的最终资源文件本身。
端到端发布的失败模式（漏拉、漏翻、占位符错配、品牌词被翻、语种贴错）在最终文件上一定有痕迹。

---

## 适用时机

仅当 diff 中**包含 l10n 资源文件改动**时触发。无 l10n 文件改动则跳过此检查。

### 文件识别（glob）

| 端 | 基准（en）路径 | 目标语种路径 |
|:---|:---|:---|
| Android | `**/res/values/strings.xml` | `**/res/values-*/strings.xml`（如 `values-zh-rCN`、`values-fr`、`values-ja`） |
| iOS | `**/en.lproj/Localizable.strings`、`**/Base.lproj/Localizable.strings` | `**/*.lproj/Localizable.strings`（除 en/Base 外） |
| Flutter | `**/intl_en.arb`、`**/app_en.arb`、`**/strings_en.arb` | 同前缀其他语种（如 `intl_zh.arb`、`app_fr.arb`） |

注意：
- 多 flavor 项目（g0-android KB/VH/VN）每个 flavor 自带一套 l10n 目录，diff 涉及哪个 flavor 就只查哪个
- 不在 diff 范围内的资源文件不主动扫描

---

## 增量审查原则（强制）

**只审查 diff 中新增/修改的 key**，不做全量历史比对。理由：
1. 历史遗留 key 不在本次 MR 责任范围
2. 全量比对会淹没本次真正的问题
3. review 性能：避免大文件全量解析

执行步骤：

1. 识别 diff 涉及的 l10n 文件，按上面 glob 分类（基准 / 目标语种）
2. 从 diff 中提取本次**新增 key**（`+<string name="...">` / `+"key" = "..."` / `+"key": "..."`）
3. 从 diff 中提取本次**修改 value 的 key**（基准或目标语种 value 改了）
4. 对这些 key 集合，读基准文件拿 en 原文，读各目标语种文件拿对应译文
5. 按下面 6 个维度逐项判断

> 如果 diff 中只新增了 en 基准 key，但目标语种文件未一并出现新增 → 命中维度 M2（漏翻），不需要去翻其他文件。

---

## 6 个检查维度

### M1 — 占位符一致性（🔴 红线 / P0）

**定义**：en 原文中的所有占位符，必须在每个目标语种译文中以**同等数量、同等类型、同等编号**出现。

**各端占位符语法**：

| 端 | 占位符示例 |
|:---|:---|
| Android | `%s`、`%d`、`%1$s`、`%2$d`、`%1$.2f`、`<xliff:g id="..." example="...">...</xliff:g>` |
| iOS | `%@`、`%d`、`%lld`、`%1$@`、`%2$d`、`%.2f` |
| Flutter ARB | `{name}`、`{count}`、`{0}`，配套 `@key.placeholders` 元数据 |

**典型失败**：

```xml
<!-- en -->
<string name="msg_count">Hello %1$s, you have %2$d new messages</string>

<!-- fr 缺第二个占位符 -->
<string name="msg_count">Bonjour %1$s, vous avez de nouveaux messages</string>
```

→ 运行时 `String.format` 抛 `MissingFormatArgumentException` 或显示 `null`。**必拦**。

**LLM 判断准则**：
- 提取 en 占位符 set（含编号 `%1$s` 和 `%2$d` 视为不同元素）
- 提取目标语种译文同 key 的占位符 set
- set 不相等 → M1 失败
- Flutter 还要检查 `@<key>.placeholders` 元数据是否同步声明

#### M1.b — 基准占位符语法变更检测（🔴 红线 / P0）

**定义**：en 基准把同一 key 的占位符**从编号语法切换为无编号语法**（或反向），且未同步重写所有目标语种译文 → 高危变更。

**为什么是独立子维度**：

Android 无编号 `%s` / `%d` 严格按出现顺序消费参数。已有目标语种译文出于语法习惯**自然调换过参数顺序**（中文/日文/韩文/土耳其文常把数量短语前置）。一旦 en 基准从 `%1$d ... %2$s` 改成 `%s ... %s`，旧译文里的"调序"立刻从合理变成 bug —— 运行时不报错，但显示乱序文案（如 `"3 个 month 内享 2024-12-01 折扣"`）。

仅靠 M1 set 比对（数量/类型一致）会**判 PASS**，因为占位符数量没变。必须独立判断。

**检测规则**：

1. 从 diff 中识别 en 基准 value 修改的 key
2. 对每个 key 比对修改前后的占位符 set
3. 命中以下任一即触发 M1.b：
   - 修改前含 `%1$s` / `%1$d` / `%1$@` 等编号占位符，修改后改为无编号 `%s` / `%d` / `%@`
   - 反向：修改前无编号，修改后加编号
4. 对所有目标语种检查同 key 译文是否同步更新；未同步 → 报告"目标语种 X 的旧译文存在调序，运行时会乱序"

**典型失败**（来自 g0-android dogfood 真实 case）：

```xml
<!-- en 基准：从带编号改为无编号 -->
- <string name="promo_success">%1$d%% off for %2$s %3$ss. Next: %4$s</string>
+ <string name="promo_success">%s%% off for %s %ss. Next: %s</string>

<!-- zh 旧译文按中文语法调过序，本次未同步修改 -->
  <string name="promo_success">%s 个 %s 内享 %s%% 折扣。下次扣款：%s</string>
  <!-- 上线后显示："3 个 month 内享 2024-12-01 折扣。下次扣款：50%" -->
```

**修复建议**：保留编号占位符语法（`%1$d` 等），让译文可以合法调序；或在切换为无编号时**强制同时重写所有目标语种译文**严格按 en 顺序。

---

### M2 — 完整性 / 漏翻（⚠️ P1）

**定义**：diff 中**新增到 en 基准**的 key，未在所有目标语种文件中同步新增。

**判断**：
- 从 diff 中识别 en 基准文件新增的 key 列表
- 对每个目标语种文件，检查 diff 中是否有同 key 的新增
- 缺失 → M2 失败，列出"哪个语种缺哪些 key"

**快速分类信号（强烈推荐先用，节省 token）**：

在逐 key 比对前，先用 `git diff --stat` 看每个目标语种文件的新增行数，与 en 基准的新增行数对比：

- 某语种新增行数 **≈ en 新增行数**（±20%）→ 大概率全量同步，跳过逐 key 比对，仅做抽样验证
- 某语种新增行数 **远小于 en**（< 50%）→ 直接归类 M2 漏翻，列出"该语种约缺 N 个 key"，**不需要逐 key 列出全部缺失项**（让作者自己回 Crowdin 检查）
- 某语种**完全未改**（diff 为空）→ M2 高置信失败

这一招在 Crowdin pull 场景下尤其有效：当某些语种 Crowdin 上还没翻完时，pull 出来的 diff 体量与 en 会有数量级差异，量化信号比逐 key 准确且省 token。

**降级条件**：
- 项目明确声明"翻译滞后于代码"工作流（如 docs 中有说明），可降级为 P2 提示
- 默认 P1，因为缺失会触发 fallback 到 en，对非英文用户体验下降

---

### M3 — 未翻译（疑似复制 en 原文）（⚠️ P1）

**定义**：某目标语种 key 的 value 与 en 原文完全相同（trim 后），但**不是合理的"无需翻译"情况**。

**LLM 判断"是否合理无需翻译"** —— 命中以下任一即视为合理：

1. **品牌词 / 专有名词**：`Premium`、`Pro`、`KiwiBit`、`VicoHome`、`VicoNature`、`NatureHood`、`HomeScreen`、`HomeKit` 等
2. **技术缩写 / 标准名**（**仅限缩写本身，不含其衍生短语**）：`AI`、`Wi-Fi`、`SD`、`HDR`、`USB`、`PIR`、`URL`、`API`、`Bluetooth`、`FPS`、`GPS`、`SSID`、`Mbps`、`4G`、`5G` 等
3. **极短共用词**（≤ 3 字符且全 ASCII 拉丁字母）：`OK`、`No`、`Hi`、`On`、`Off`、`Go`（通常各拉丁语种共用）
4. **纯数字 / 纯符号 / 纯占位符**：`%1$s`、`{count}`、`---`、`v1.0` 等
5. **URL / 邮箱 / 文件路径 / 颜色 hex**：`https://...`、`#FF0000` 等
6. **目标语种本就借用英文**：日文 `カメラ` 或片假名借用英文外来语，但 value 直接是 `Camera` 而非 `カメラ` → 仍判为未翻译

**关键区分：缩写 vs 功能短语 / 句子**（来自 dogfood 反馈，必须明确）：

- ✅ **可豁免**：value 就是裸缩写或缩写组合，无其他描述性文字
  - `HDR` / `Wi-Fi` / `4G LTE` / `USB-C`
- ❌ **不可豁免**（必须翻译）：缩写嵌入在功能描述短语或句子里
  - `Ultra Slow Motion` —— "Ultra" / "Slow" / "Motion" 都是普通形容词/名词，不是缩写
  - `HDR Preview` —— `HDR` 本身豁免，但 "Preview" 必须翻译，整体不豁免
  - `Enable HDR mode for better contrast` —— 含完整句子结构，必须翻译
  - 含介词、动词、定冠词的多词短语（`for / with / the / a / and / to`）→ 一定不是缩写，必须翻译

**判定流程**：
1. 先 trim value，看是否完全等于 en 原文 → 不等就跳过
2. 提取 value 的"非占位符词"set
3. 词数 == 1 且属于第 1/2 类豁免词 → 通过
4. 词数 > 1 → 检查是否含介词/动词/定冠词 → 含 = 必须翻译，不豁免；不含 = LLM 谨慎判断（保守倾向"通过"并备注）

**LLM 判断指南**：
- 优先信任"译文 == 原文"是 fail，再用上面 6 条豁免
- 不在白名单内但语义上是品牌/专名（如新产品名 `EagleEye`），LLM 自行判断时**保守倾向"通过"**，并在备注中说明"假定为品牌词，如非请补充翻译"
- 拉丁字母语种（fr/es/de/it）之间，部分单词天然同形（如 `Animation`、`Photo`），LLM 见到时**保守倾向"通过"**并提示

**典型失败**：

```xml
<!-- en --> <string name="welcome_msg">Welcome to your home</string>
<!-- fr --> <string name="welcome_msg">Welcome to your home</string>
```

→ M3 失败：fr 译文是英文原文，且不属于豁免类别。

---

### M4 — 语种不匹配（⚠️ P1）

**定义**：某目标语种文件的 value 内容**不是该语种的语言**。

**LLM 判断步骤**：
1. 取目标语种文件标识（如 `values-fr` → 法文，`zh-Hans.lproj` → 简体中文，`intl_ja.arb` → 日文）
2. 读 value 内容判断语种归属
3. 不一致 → M4 失败

**判断置信度规则**：
- **高置信场景**（必报）：
  - 目标语种是 CJK（中/日/韩）/ 阿拉伯文 / 西里尔字母 / 泰文 / 希伯来文 / 希腊文等独特字符体系，但 value 全是 ASCII 拉丁字母（且不属于 M3 豁免词）
  - 或反过来：fr/es/de 等拉丁语种文件中出现大段 CJK / 阿拉伯字符
- **低置信场景**（不报，避免误判）：
  - 拉丁字母语种之间（fr ↔ es ↔ de ↔ it ↔ pt）短文案的微妙差异
  - value 长度 < 20 字符的拉丁语种文案
- **对照 M3**：M4 命中时通常 M3 也会命中，但 M4 的提示更具体（"贴错语种"而非"未翻译"）

**典型失败**：

```xml
<!-- values-fr/strings.xml --> <string name="alert_motion">检测到运动</string>
```

→ M4 失败：fr 文件出现中文。可能是录入 Crowdin 时贴错语种或合并冲突误处理。

---

### M5 — HTML / 转义标签一致性（🔴 红线 / P0）

**定义**：en 原文中的标签结构（HTML / Android 富文本 / 转义符），必须在目标语种译文中**结构一致**。

**关注的标签 / 转义**：

| 端 | 标签 / 转义 |
|:---|:---|
| Android | `<br/>`、`<b>`、`<i>`、`<u>`、`<font color="...">`、`<a href="...">`、`&amp;`、`&lt;`、`&gt;`、`\n`、`\'`、`\"` |
| iOS | `\n`、`%%`（转义为字面量 %）、`<br/>`（如使用 NSAttributedString HTML 解析）|
| Flutter ARB | `\n`、Markdown 标记（如 `**bold**`，需配合渲染） |

**判断**：
- 提取 en 中标签序列（按顺序、含开/闭）
- 比对目标语种同 key 的标签序列
- 不一致（缺标签、闭合错、href 内容被翻）→ M5 失败

**特别警惕**：`<a href="https://...">` 内部的 URL 不应被翻译；`<xliff:g>` 内 placeholder name 不应被翻译。

---

### M6 — 空值检查（⚠️ P1）

**定义**：key 存在但 value 为空字符串（或仅空白字符）。

**典型失败**：
```xml
<string name="error_network"></string>
```

→ App 显示空白，用户无任何提示。

**判断**：trim 后 value 为空 → M6 失败。少数特殊场景（如纯占位符 ` ` 用于排版）允许，但要求 reviewer 确认意图。

---

## 输出格式

在 code-review 5 段输出的 **§3 内容质量** 中追加 i18n 子节，使用如下表格：

```markdown
**i18n 资源文件**（基于 diff 增量审查）

涉及文件：
- 基准（en）：`<file path>` — 新增 N 个 key、修改 M 个 value
- 目标语种：`<file path>` × K 个文件

| 维度 | 严重级 | 文件 | key | 问题描述 |
|:---|:---|:---|:---|:---|
| M1 占位符 | 🔴 P0 | values-fr/strings.xml | msg_count | en 有 `%1$s %2$d`，fr 仅 `%1$s` |
| M2 漏翻 | ⚠️ P1 | values-ja/strings.xml | (5 个 key) | 见列表 |
| M3 未翻译 | ⚠️ P1 | values-fr/strings.xml | welcome_msg | value == en 原文 |
| M4 语种 | ⚠️ P1 | values-fr/strings.xml | alert_motion | fr 文件出现中文「检测到运动」 |
| M5 标签 | 🔴 P0 | values-de/strings.xml | tip_html | en 含 `<b>`，de 缺失 |
| M6 空值 | ⚠️ P1 | values-zh-rCN/strings.xml | error_network | value 为空 |

无问题语种：values-es、values-pt、values-ru ✅
```

如全部维度通过，仅输出一行：
> **i18n 资源**：本次涉及 N 个 l10n 文件，6 项检查全部通过 ✅

---

## 与 Step 4 红线的关联

以下命中直接触发 Step 4 红线（结论必须为"不通过"）：

- **M1 占位符不一致**：会导致运行时崩溃或显示异常，等同已实现功能但无质量保障
- **M5 标签结构不一致**：会导致富文本渲染断裂，等同已实现功能但无质量保障

其余维度（M2 漏翻、M3 未翻译、M4 语种、M6 空值）默认 P1，记入"建议改进"，不阻断合并；但如果是核心用户路径文案（错误提示、付费转化、关键 CTA），reviewer 可酌情升级为红线。

---

## LLM 执行提示

执行此检查的 sub-agent 应：

1. **先列 diff 涉及的 l10n 文件清单** —— 没有就直接跳过整节检查
2. **再分别 Read 基准文件和每个目标语种文件**（只读涉及的 key 周围片段，无需全文）
3. **逐 key 逐维度过 6 项检查**，不要批量泛泛判断
4. **每个发现都要给出 file path + key + 原文/译文片段**，便于 reviewer 直接定位
5. **判断"未翻译"和"语种"时倾向保守**（宁漏报不误报），并在备注里说明假设
6. **不要去查 Crowdin、不要去查代码引用** —— 这一节就只对最终资源文件负责
