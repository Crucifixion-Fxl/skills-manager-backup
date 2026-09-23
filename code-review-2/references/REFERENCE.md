# Code Review - 参考文档

> 本文档是 SKILL.md 的补充参考。

## 审查标准来源

本 skill 的审查标准全部来自 `/architect` skill：
- 文档规范：核心原则 1-7、跨文档维护规约
- 架构质量：Step 2c 架构质量原则（结构性/健壮性/可演进性）
- 测试标准：Step 5 质量标准
- 可观测性：Step 2b 可观测性追问 + Step 4 可观测性设计

## 抽象思维与 Code Smell 培训

- [`abstraction-thinking-training.md`](abstraction-thinking-training.md)：面向员工的抽象思维训练文档，解释为什么复杂 `if/else`、隐式状态机、重复条件和职责过重函数会提高认知负荷，以及如何通过命名、guard clause、变化轴识别、Policy/State Machine/DDD 等方式训练抽象能力。

## i18n 资源文件质量审查

- [`i18n-quality-review.md`](i18n-quality-review.md)：当 diff 涉及 Android/iOS/Flutter 的 l10n 资源文件时触发的 6 维度增量检查（占位符一致性、漏翻、未翻译、语种匹配、HTML 标签、空值）。LLM-driven，不查 Crowdin、不查代码引用，只对最终落地资源文件负责。

## app UI 无障碍 + 可测试性审查

- [`app-ui-a11y-testability-review.md`](app-ui-a11y-testability-review.md)：当 diff 涉及 app UI 源文件（iOS `*.swift` / Android `layout*.xml`·`*.kt` / Flutter `*.dart`）时触发的双轴增量检查。**可测试性轴（T）硬卡**：交互控件缺稳定测试标识（`accessibilityIdentifier`/`testTag`/`Key`），T1 高置信进红线。**无障碍轴（A）阶段一试跑**（全 ⚠️ 不阻断）：缺可读名、装饰未排除、字号不可缩放、热区过小、输入缺标签；阶段二把 A1/A5 高置信升 🔴。查标注存在性而非质量，读整份文件消跨行误报，区分 `accessibilityIdentifier`(测试) ≠ `accessibilityLabel`(朗读)。

## L3 staging / release 分阶段门禁

- [`l3-release-gate.md`](l3-release-gate.md)：L3 分阶段门禁。非 release review 中，只有代码/测试事实证明依赖已部署 staging 的 L3 才可延后且不阻断；release context 必须具备绑定 revision 且执行成功的 L3 report。automation capability 只证明可执行性，未运行时仍以 `RELEASE_L3_EXECUTION_UNVERIFIED` 阻断。

## 代码中文字面量拦截

- [`code-chinese-literal-review.md`](code-chinese-literal-review.md)：当 diff 涉及源码文件（`*.go`/`*.ts`/`*.tsx`/`*.js`/`*.jsx`/`*.java`/`*.kt`/`*.swift`/`*.dart`/`*.py` 等，排除 i18n 资源文件与 `*.md`/docs）时触发的增量检查。**C1**：源码字符串字面量含中文（`\p{Han}` 或中文标点）—— 非豁免高置信子集覆盖用户可见文案/接口返回值/日志/枚举/普通测试数据/品牌名硬编码；只有随 Skill 分发的 `references/code-review-policy.yaml` 中 `blocking_namespaces` 后代项目或 `blocking_projects` 精确项目才进红线 🔴，范围外高置信项及注释·docstring·外部绑定·生成文件·测试意图不明均为 ⚠️。i18n 资源文件，以及 test-only 且明确验证中文 locale / 多语言文案的期望值、fixture、snapshot/golden 完全豁免。LLM-driven，读整份源文件并结合测试语义消除误报。

## 变更获取方式详解

### 远程 MR

```bash
# 获取 MR diff
curl -s --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/{id}/merge_requests/{iid}/changes"

# 获取 MR commits
curl -s --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/{id}/merge_requests/{iid}/commits"
```

需要先通过项目搜索 API 获取 project ID：
```bash
curl -s --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects?search={project_name}&simple=true"
```

### 本地已提交

```bash
# 当前分支与 main 的差异
git diff main..HEAD --name-only      # 文件列表
git diff main..HEAD                  # 完整 diff
git log main..HEAD --oneline         # commit 列表
```

### 本地未提交

```bash
git diff --name-only                 # 未暂存变更
git diff --staged --name-only        # 已暂存变更
git status                           # 总览
```

## 端到端一致性审查示例

### 文档同步时效

- 代码改变可观察行为时，当前 MR 必须同步更新当前行为文档，或给出证据证明既有文档仍准确。
- “后续补文档”“另开 MR”或 TODO 不能作为通过理由；文档缺失、过时或仍描述旧行为，均按 `documentation-code drift` 阻断。
- `planned` / `target state` 只适用于尚未实现的未来行为；代码落地后文档仍停留在目标态，同样属于文档过时，必须在当前 MR 更新。

以 MR !52（鸟类图鉴 schema 更新）为例：

| 变更 | US | 架构设计 | 功能代码 | 测试 | 可观测 | 状态 |
|:-----|:---|:--------|:--------|:-----|:------|:-----|
| species_reference 改造 | US-CL-04 | overview.md §4.1 | species.go | species_test.go | observability.md | ✅ |
| species_content 新表 | US-CL-09 | overview.md §4.2（标注 `planned`） | 未实现 | — | — | ✅ 目标态，不计入当前实现 |
| keyshots JSON 字段 | US-CL-10 | overview.md §4.5 | event.go 仍用 keyshot_url | — | — | 🔴 documentation-code drift |

关键判断：
- species_content 文档明确标注 `planned`，代码没有 → ✅ 目标态，不宣称已实现
- keyshots 文档说 JSON 但代码用 keyshot_url → 🔴 已实现部分的描述与代码不一致

## 六大支柱（历史参考）

原 dev-standards-review 的六大支柱已整合：
- P1 架构 → `/architect` Step 2c 架构质量原则
- P2 CI/CD → `/architect` Step 6
- P3 可测性 → `/architect` Step 5
- P4 可观测性 → `/architect` Step 2b/4b
- P5 合规安全 → `security-compliance-review` skill
- P6 性能 → `/architect` Step 2b 设计约束（SLA 相关）
