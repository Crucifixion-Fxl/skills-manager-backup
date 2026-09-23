# 代码中文字面量拦截审查

用于 code-review Step 2（内容质量审查），对 MR 源码做**中文字符串字面量增量检查**。检测在所有项目执行，但是否阻断由随 Skill 分发的中央范围配置决定：只有 [`code-review-policy.yaml`](code-review-policy.yaml) 的 `blocking_namespaces` 后代项目或 `blocking_projects` 精确项目，非豁免的高置信 C1 才是红线；范围外项目的相同发现仅作非阻断警告。i18n 资源文件，以及专门验证中文 locale / 多语言文案的 test-only 字面量豁免。

**核心思想**：和 i18n 资源审查同源 —— 不查运行时、不查测试报告，直接审查 diff 里落地的源码。"硬编码中文绕过 i18n"「接口返回值混入中文」这类失败模式，在最终源码里一定有痕迹（CJK 字符）。

> **检测事实与门禁策略必须分开。** “字面量内是否有 CJK 字符”可以机械判断；“是否属于字符串、是否豁免”需要读上下文；“是否阻断”只由中央清单决定。业务仓内的本地配置、注释或 MR 描述不能自行启用或关闭这条门禁。

---

## 适用时机

仅当 diff 涉及**源码文件**时触发。**只查 diff 新增/修改的行**，不全量扫历史中文。

### 项目级门禁策略（先判 policy）

1. 读取中央配置 [`code-review-policy.yaml`](code-review-policy.yaml) 的 `rules.chinese_literal.blocking_namespaces` 与 `blocking_projects`。
2. 按以下优先级解析当前项目：注入的 `ci_project_path` → `$REVIEW_DATA_DIR/meta.json` 中的 `project.path_with_namespace` / `project_path` → `$CI_PROJECT_PATH` → 本地 `origin` remote。remote URL 的 host 必须等于受信的 `$CI_SERVER_HOST` 或本地显式 `--gitlab-host`；未注入受信 host、或其他 Git 主机均返回 `unknown`。
3. 本地或环境有 Python 时，运行 [`../scripts/resolve_project_path.py`](../scripts/resolve_project_path.py)（从 Skill 资源目录解析，不能按被审仓 cwd 猜路径）。主 CI 无 Python，必须用 Read 读取 meta 并按下表得到相同结果；禁止违反 `ci-integration.md` 临时编写 Python 脚本。

| 输入形式 | 示例 | canonical project |
|:---|:---|:---|
| 已归一化路径 | `Engineering/Skills` | `engineering/skills` |
| HTTPS remote | `https://gitlab.addx.ai/Engineering/Skills.git` | `engineering/skills` |
| SSH URL | `ssh://git@gitlab.addx.ai/Engineering/Skills.git` | `engineering/skills` |
| SCP remote | `git@gitlab.addx.ai:Engineering/Skills.git` | `engineering/skills` |
4. `blocking_projects` 只做完整路径精确匹配；`blocking_namespaces` 只匹配“namespace + `/`”开头的后代项目，保留路径段边界。不支持 glob、正则、未配置 namespace 推断或项目内覆盖。

| 策略解析结果 | `policy` | 高置信 C1 的级别 |
|:---|:---|:---|
| canonical project 属于 `blocking_namespaces` 后代 | `block` | 🔴 P0，确定性红线 |
| canonical project 精确列入 `blocking_projects` | `block` | 🔴 P0，确定性红线 |
| canonical project 不在上述范围 | `warning` | ⚠️ 非阻断警告 |
| 中央配置缺失/无效/不可读，或无法确定项目身份 | `warning` | ⚠️ 非阻断警告，并报告策略解析诊断 |

中央配置本身由本仓 schema 和 CI 契约测试保护。审查运行时无法解析策略时不得猜测为 `block`；应显式给出诊断，避免把配置故障误判成业务代码红线。

### 文件识别

**触发（按源码处理）**：iOS `*.swift`、Android/Java/Kotlin `*.kt`·`*.java`、Go `*.go`、TS/JS `*.ts`·`*.tsx`·`*.js`·`*.jsx`、Dart `*.dart`、Python `*.py` 等承载业务逻辑的源码。**前置：先按路径排除 `docs/**` 整棵树——路径优先于扩展名**，`docs/**` 下的 `.py`/`.js` 等是文档工程而非业务源码，不触发（见下方豁免）。

**豁免（完全不报）**：

- **i18n 资源文件**：Android `**/res/values*/strings.xml`（含 `values-zh*/`）、iOS `**/*.lproj/Localizable.strings`、Flutter `**/*.arb`（含 `*_zh.arb`/`zh.arb`）、Crowdin 导出/译文文件。中文译文是这些文件的**合法内容**（zh 文件里有中文是对的），整体交给 [`i18n-quality-review.md`](i18n-quality-review.md) 按其 6 维度负责，本节**不触发、一律不报**。
- **纯文档 / Markdown 与文档工程（按路径，整棵 `docs/**` 树）**：`*.md`、`docs/**` 路径下的**一切**、设计文档 —— 中文是文档的正常语言。**关键：`docs/**` 是按路径豁免，落在该路径下的文件即便扩展名是 `.py`/`.js`（如文档站构建工具、MD→HTML 生成器）也豁免**，其中的中文是面向人类读者的文档 UI 文案/标签，不是产品运行时字符串；**不因扩展名把 `docs/**` 下的脚本回退按业务源码处理**。
- **多语言文案测试 oracle**：测试代码或测试专用 fixture/snapshot/golden 中，为验证中文 locale、i18n/l10n 翻译、占位符、locale 路由/回退、资源解析或渲染结果而出现的中文期望值/输入样例。必须同时满足下方“测试场景豁免”判定，不能仅凭测试文件路径豁免。

> 触发条件与 i18n 模块**正交**：i18n 模块只在资源文件触发、管译文质量；本节在 `docs/**` 之外的源码文件触发、管源码里不该出现的中文字面量，并在逐处判定时放行合法的多语言文案测试 oracle。i18n 资源文件与整棵 `docs/**` 树仍由本节按文件整体排除。

---

## 增量审查原则（强制）

- **只查 diff 新增/修改行**：不对历史已存在的中文翻旧账（除非该行被本次 diff 修改）。
- **读整份文件**：CI 环境文件在盘上，必须 `Read` 整份源文件，确认命中的中文到底在「字符串字面量」还是「注释」里 —— 消除跨行误报（如多行字符串、跨行注释）。
- **逐处判定**：对每一处 CJK 命中单独判定轴向（高置信字面量候选 / 注释 ⚠️ / 豁免不报），不要批量泛泛下结论。
- **高精确识别候选**：高置信 C1 的前提是"确为字符串字面量 + 非 i18n 资源文件 + 非外部绑定 + 非生成文件 + 非多语言文案测试 oracle"；**任一不确定 → 降 ⚠️ 备注**（宁漏报不误报）。候选只有在 `policy=block` 时才进入红线。

---

## 检查维度

### C1 — 代码中文字符串字面量（高置信候选；级别由 policy 决定）

**定义**：源码中**字符串字面量**内含中文字符（`\p{Han}` 汉字，或中文标点 `，。！？、；：""''（）《》【】` 等）。包括但不限于：用户可见硬编码文案、API response/DTO 字段默认值、错误消息、日志参数、枚举值、常量、map/字典 value、配置默认值。

**各端字符串字面量与注释语法**：

| 语言 | 字符串字面量 | 注释（⚠️ 不进红线） |
|:---|:---|:---|
| Go | `"..."`、反引号 raw string `` `...` `` | `//`、`/* */` |
| TS / JS | `"..."`、`'...'`、模板串 `` `...` `` | `//`、`/* */` |
| Java / Kotlin | `"..."`、Kotlin `"""..."""` | `//`、`/* */` |
| Swift | `"..."`、`"""..."""` | `//`、`/* */` |
| Dart | `"..."`、`'...'`、raw `r"..."` | `//`、`/* */`、`///` |
| Python | `"..."`、`'...'`、`f"..."`、`"""..."""` | `#`、docstring（按注释类，⚠️） |

> **Python `"""..."""` 的双重身份**：作为函数/类/模块的**第一条语句**时是 docstring → 按注释类 ⚠️；出现在**其他位置**（赋值右侧、`return`、函数参数等）时是字符串字面量 → 高置信 C1 候选。同形不同判，按位置区分。
>
> **f-string 字面量部分**：`f"用户 {name} 你好"` 中 `用户`/`你好` 是字面量文本 → 命中高置信 C1；`{name}` 是插值表达式、不算文案。只要 f-string 的**字面量段**含中文就报，再按项目 `policy` 决定 🔴 或 ⚠️。
>
> **多行 raw string**（Go 反引号、Python/Kotlin `"""`）：diff hunk 可能只露出含中文的一行 —— **必须读整份文件**确认它是字符串字面量而非跨行注释，再判是否属于高置信 C1。

**高置信 C1 候选判定—— 全部满足才成立**：
1. 命中的中文位于**字符串字面量**内（非注释、非 docstring）；
2. 文件**非** i18n 资源文件、**非**生成文件（`*.pb.go`/`*.g.dart`/`*_generated.*`/`*.freezed.dart` 等）；
3. **非疑似**与外部 DB/协议/第三方系统硬绑定（即"改成英文不会破坏外部兼容"）。
4. **不属于**下方“测试场景豁免”。

满足四条 → 确认为高置信 C1 候选：`policy=block` 时为 🔴 P0；`policy=warning` 时为 ⚠️。

**⚠️ 判定（不阻断）**：
- **注释 / docstring 中文**：⚠️ 提示，推动习惯走全英文，但**不进红线、不阻断**。
- **疑似外部绑定**：作者证明该中文与外部 DB 枚举值、对端协议字段、第三方系统返回值硬绑定、改了破兼容 → 降 ⚠️ + 备注「疑似外部绑定，请确认」。
- **生成文件**：源在别处，改源不改生成物 → 降 ⚠️ + 备注「生成文件，请改生成源」。
- **拿不准**（疑似在注释、疑似生成、疑似外部绑定、跨行无法确证）→ 降 ⚠️ 备注。
- **测试意图不明**：中文位于测试代码，但无法从测试名/路径、locale 设置、被测 API 或断言语义确认它是否专门验证多语言文案 → 降 ⚠️，要求作者补清测试意图。

#### 测试场景豁免（两项同时满足）

1. **test-only 载体**：命中位于测试代码或测试专用 artifact，例如 `*_test.*`、`*.test.*`、`*.spec.*`、`test/**`、`tests/**`、`__tests__/**`、`e2e/**`、`androidTest/**`、`*Tests/**`、`*UITests/**`，以及这些测试使用的 fixture/snapshot/golden；不会作为生产运行时代码或默认配置发布。
2. **多语言测试语义明确**：测试名/路径、locale 设置或被测 API 明确指向 `zh`/`zh-CN`/`zh-Hans` 等中文 locale 或 i18n/l10n/translation 行为，并把中文用作翻译期望值、占位符结果、locale 路由/回退结果、资源解析输入或渲染 snapshot/golden。

两项都满足 → **不触发 C1、不套用项目 policy、不报 finding**。中文是测试 oracle，改成英文会让测试失去对中文文案的验证能力。

以下仍**不豁免**：

- 普通业务单测仅因方便而使用中文姓名、状态、错误文本或随手 fixture；
- 用中文断言固化生产代码中的硬编码中文，而被测行为并非 i18n/l10n；
- 测试 helper/fixture 会进入生产包、运行时默认配置或对外协议；
- 品牌名/专有名词硬编码，但测试并非在验证该词的本地化结果。

中文文案作为 UI 自动化 selector 时也必须同时满足上述两项条件（例如专门验证 `zh-CN` 的 UI E2E）才豁免；普通 UI 测试中的 `getByText("提交")` / `find.text("提交")` 不豁免。本规则只判 C1，不把本地化文案 selector 视为稳定定位标识；不得声称现有 App UI T2 已覆盖测试查询 API。

**典型失败**：

```go
// Go：接口返回值硬编码中文 → 高置信 C1
return Resp{Code: 400, Msg: "参数错误"}   // policy=block 时 🔴，否则 ⚠️

// Go：日志参数中文 → 高置信 C1
log.Errorf("用户绑定失败: %v", err)        // policy=block 时 🔴，否则 ⚠️

// 注释中文 → ⚠️ 不进红线
// 校验用户输入                            // ⚠️ 提示，不阻断
```

```dart
// Dart：用户可见硬编码文案（应走 i18n）→ 高置信 C1
Text('提交')                              // policy=block 时 🔴，否则 ⚠️
```

```ts
// TS：枚举/常量中文 → 高置信 C1
const STATUS = { active: '启用', inactive: '停用' };   // policy=block 时 🔴，否则 ⚠️
```

```ts
// ✅ 专门验证 zh-CN 翻译：中文是期望值，C1 豁免且不套用 policy
it('renders the zh-CN submit label', () => {
  expect(translate('submit', { locale: 'zh-CN' })).toBe('提交');
});

// 普通业务测试随手使用中文数据：仍形成高置信 C1 候选
it('creates a user', () => createUser({ displayName: '张三' }));
// policy=block 时 🔴，否则 ⚠️
```

---

## 输出格式

在 code-review 5 段输出的 **§3 内容质量** 中追加子节（仅当本检查有发现时）：

```markdown
**代码中文字面量拦截**（基于 diff 增量审查）

项目策略：`canonical_project=<group/project>`，`policy=<block|warning>`，依据：`<namespace 命中/精确项目命中/范围外/解析失败诊断>`

涉及文件：`<file>` × N

| 严重级 | 文件:行 | 位置 | 中文片段 | 说明 |
|:---|:---|:---|:---|:---|
| 🔴 P0 | api/handler.go:88 | 字符串字面量（返回值） | "参数错误" | `policy=block`；接口返回值硬编码中文，应走错误码/i18n |
| ⚠️ | ui/login.dart:42 | 字符串字面量（UI 文案） | "提交" | `policy=warning`；用户可见文案建议走 l10n，不阻断 |
| ⚠️ | svc/user.go:15 | 注释 | // 校验用户输入 | 注释中文（不阻断，建议全英文）|
| ⚠️ | model/order.go:30 | 字符串字面量（外部绑定） | "已支付" | 疑似与 DB 枚举绑定，请确认能否改 |
```

无违规中文命中时，仍应先给出项目策略，再输出一行：
> **代码中文字面量拦截**：本次涉及 N 个源码文件，无违规中文字符串字面量 ✅（多语言文案测试豁免 K 处）。

---

## 与 Step 4 红线的关联

**只有以下两个条件同时满足才进红线**（结论必须为“不通过”）：

1. 当前 canonical project 属于中央 `blocking_namespaces` 后代或精确列入 `blocking_projects`，即 `policy=block`；
2. 命中高置信 C1：源码字符串字面量含中文，且非 i18n 资源文件、非生成文件、非外部绑定、非多语言文案测试 oracle。

**不进红线（记入“建议改进”）**：阻断范围外项目的高置信 C1、策略解析失败、注释/docstring 中文、疑似外部绑定的字面量中文、生成文件中文、测试意图不明，以及任一高置信条件不确定的 ⚠️ 项。明确命中多语言文案测试豁免的字面量不报 finding。

---

## LLM 执行提示

执行此检查的 sub-agent 应：

1. **先解析中央项目策略**：输出 canonical project、`policy=block|warning` 和判定依据；失败时输出诊断并使用 `warning`。
2. **再列 diff 涉及的源码文件清单** —— **按路径**排除 i18n 资源文件与整棵 `docs/**` 树（含其下的 `.py`/`.js` 文档工具，路径优先于扩展名），没有 `docs/**` 之外的源码文件就跳过发现扫描。
3. **逐行扫 CJK 命中**：`\p{Han}` 或中文标点。
4. **读整份相关源文件**（不止 hunk），确认每处命中在「字符串字面量」还是「注释」—— 消除跨行误报。
5. **逐处判轴向**：高置信 C1 候选 / 注释（⚠️）/ i18n 资源文件（不报）/ 多语言文案测试 oracle（不报）。
6. **先判候选、后套 policy**：高置信条件全部满足后，`block` 才标 🔴；`warning` 标 ⚠️。任一上下文不确定直接降 ⚠️。
7. **测试文件按语义判定**：先确认 test-only 载体，再从测试名/路径、locale 设置、i18n/l10n API、断言/fixture/snapshot 语义确认是否专门验证多语言文案；两项满足才豁免且不套用 policy。普通测试数据、品牌名硬编码中文照常形成候选；意图不明降 ⚠️。
8. **每个发现给出 file:line + 位置类型 + 中文片段**，便于直接定位。
9. **修复方向提示**：用户可见文案 → 走 i18n（l10n/strings 资源）；接口返回值 → 走错误码 + i18n 文案；`policy=block` 项目的日志/内部消息 → 改英文；`policy=warning` 项目只给建议，不阻断。

> **已知漏报边界（本版本不扫，宁漏报不误报）**：用 Unicode 转义书写的中文（如 `"参数"` = "参数"）在字节层无 CJK 字符、`\p{Han}` 扫不到，本版本不作还原扫描；若 review 时恰好注意到此类写法可顺手 ⚠️ 提示，但不作为强制项。
