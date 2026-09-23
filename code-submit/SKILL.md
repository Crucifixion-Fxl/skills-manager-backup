---
name: code-submit
description: 跨平台代码提交全流程：Lint检查 → 非破坏性Review → 人工校验文档 → 智能暂存 → 干净提交 → MR创建。自动检测项目类型（Android/iOS/后端），适配对应的 lint/build 工具。当用户说"提交代码"、"准备提交"、"submit"、"开始提交流程"、"code submit"时触发。
argument-hint: "--target=<目标分支> [--from=stage1-6] [--type=android|ios|backend]"
---

# 跨平台代码提交全流程

串联 6 个阶段，每个阶段有用户确认门控。核心原则：**lint 先行，review 不改逻辑，人工校验兜底，干净提交上线。**

前提：当前仓库 remote 指向 `gitlab.addx.ai`，已安装 `glab` CLI。

---

## 参数解析

从 `$ARGUMENTS` 中解析：
- `--target=<branch>`：目标分支（**必填**），如 `--target=test/VH_2.92.0_20260310140519`
- `--from=<stage>`：从指定阶段开始（可选），值为 `stage1` 到 `stage6`，跳过前面的阶段
- `--type=<android|ios|backend>`：强制指定项目类型（可选，默认自动检测）

如果未提供 `--target`，**必须询问用户**目标分支后再继续。

---

## Step 0：项目类型检测

自动检测当前项目类型，决定后续阶段使用的具体命令。

### 检测规则（按优先级）

1. **Android**：存在 `build.gradle` 或 `build.gradle.kts` 且存在 `**/AndroidManifest.xml`
   - Lint：`./gradlew :<module>:lint --no-daemon`
   - 测试目录模式：`src/test/`, `src/androidTest/`
   - 排除目录：`build/`, `.idea/`, `.gradle/`, `debugpanel/`, `*.iml`

2. **iOS**：存在 `*.xcodeproj` 或 `*.xcworkspace` 或 `Package.swift`
   - Lint：`swiftlint lint --reporter json`
   - 测试目录模式：`*Tests/`, `*UITests/`
   - 排除目录：`DerivedData/`, `.build/`, `Pods/`, `*.xcuserstate`

3. **后端（按语言细分）**：
   - **Java/Kotlin (Spring Boot)**：存在 `pom.xml` 或（`build.gradle` 但无 `AndroidManifest.xml`）
     - Lint：`./gradlew checkstyleMain` 或 `./mvnw checkstyle:check`
   - **Python**：存在 `pyproject.toml` 或 `requirements.txt`
     - Lint：`ruff check .` 或 `flake8`
   - **Go**：存在 `go.mod`
     - Lint：`golangci-lint run`
   - **Node.js**：存在 `package.json`（无上述特征文件）
     - Lint：`npm run lint` 或 `npx eslint .`

4. **多栈 Monorepo**（customer-care 模式）：同时存在 `go.mod` + `app/*/pubspec.yaml`（Flutter）+ `*/package.json`（Node）+ `*/pyproject.toml`（Python）
   - Lint 并行跑所有栈：`golangci-lint run` + `flutter analyze` + `npm run lint` + `ruff check`
   - 任一失败阻塞提交
   - 测试目录：`internal/**/*_test.go`、`*/test/`、`e2e/`
   - 排除：`node_modules/`、`.gradle/`、`.tmp/`、`.logs/`

检测后输出确认：
```
项目类型：Android（依据：build.gradle + AndroidManifest.xml）
Lint 命令：./gradlew :<module>:lint --no-daemon
测试目录：src/test/, src/androidTest/
```

如果用户通过 `--type` 强制指定，跳过自动检测。

---

## Stage 0.5：Dev 健康预检

继承 `superpowers:verification-before-completion` 的 Iron Law。

在 Lint 之前先验证本地开发环境真实运行过代码：
1. 存在 `Makefile` + `make dev-check` target：执行 `make dev-check`
   - 所有服务 ✓ 通过才继续
   - 任一 ✗ 停止，提示"请先 make dev 启动环境并本地验证后再提交"
2. 无 Makefile 的项目：跳过此 Stage（兼容）

**为什么**：避免推未本地验证的代码入 CI，把 CI 当第一道防线而非最后一道。

---

## Stage 1：Lint 检查

> 目标：在 code review 之前先过 lint，避免 CI 因 lint 反复失败

### 1a. 检测变更范围

```bash
TARGET_BRANCH="<从参数解析>"
git fetch origin "$TARGET_BRANCH" 2>/dev/null
CHANGED_FILES=$(git diff --name-only "origin/$TARGET_BRANCH"...HEAD)
```

如果 `CHANGED_FILES` 为空，提示用户 "相对于目标分支没有变更文件" 并结束。

### 1b. 执行 Lint（按项目类型）

**Android**：
1. 从变更文件路径推导 Gradle 模块：
   - `app/src/...` → `:app`
   - `BaseLib/xxx/src/...` → `:BaseLib:xxx`
   - `ThirdPartLib/xxx/src/...` → `:ThirdPartLib:xxx`
   - 无法映射的文件 → 回退到 `:app:lint`
2. 对每个变更模块执行（timeout: 300000）：
   ```bash
   ./gradlew :<module>:lint --no-daemon
   ```

**iOS**：
1. 筛选变更的 `.swift` 文件
2. 执行：
   ```bash
   swiftlint lint --path <file1> --path <file2> --reporter json
   ```

**后端**：执行 Step 0 中确定的 lint 命令（timeout: 120000）。

### 1c. 解析结果

**Android**：读取 `build/reports/lint-results*.xml`，提取 `severity="Error"` 条目。
**iOS**：解析 SwiftLint JSON 输出中 `severity: "error"` 条目。
**后端**：解析对应工具的输出。

对每个 error 列出：文件路径 + 行号 + 错误类型 + 描述。

### 1d. 处理错误

- **有 Error**：展示错误列表，尝试自动修复简单问题（import 排序、格式化、unused import）。修复后重新 lint 验证。如果仍有 error，列出剩余问题，询问用户是否继续（带风险提示）。
- **无 Error**：通过。Warning 仅展示不阻断。

### 1e. 门控

```
Lint 检查结果：0 errors, N warnings
是否继续进入 Code Review？
```

等待用户确认。

---

## Stage 2：非破坏性 Code Review

> 目标：只做质量审查，绝不改变用户设计好的业务流程

### 2a. 收集变更

```bash
git diff "origin/$TARGET_BRANCH"...HEAD -- '*.kt' '*.java' '*.swift' '*.py' '*.go' '*.ts' '*.js' ':!*Test*' ':!*test*' ':!*spec*' ':!*Mock*'
```

只 review 生产代码，排除测试文件。

### 2b. 执行 Review

对每个变更文件的 diff 进行审查，遵循以下**不可违反的规则**：

#### 绝对禁止修改的内容

以下内容代表用户的设计决策，**只能提出疑问，不能修改**：

- **控制流逻辑**：if/when/for/while/switch 的条件和分支结构
- **API 调用顺序**：网络请求、数据库操作、SDK 调用的调用链和顺序
- **状态管理**：StateFlow/LiveData/Combine/Redux/MobX 等状态流的发射和订阅
- **方法签名**：参数列表、返回类型、方法名、访问修饰符
- **类层级结构**：继承关系、接口实现、协议遵从、泛型约束
- **业务规则**：价格计算、权限判断、流程跳转条件、数据校验规则
- **架构决策**：设计模式选择、模块划分、依赖注入方式、数据流方向

#### 可以建议的内容（输出为建议，由用户决定是否采纳）

- 变量/方法/类的命名改进
- 代码风格和格式一致性
- 潜在的空指针 / 资源泄漏 / 内存泄漏
- 性能问题（不必要的对象创建、N+1 查询、主线程阻塞操作）
- 安全隐患（硬编码密钥、注入风险、明文存储敏感数据）
- 重复代码提醒
- 缺失的错误处理（仅在现有 try-catch 范围内补充）
- 未关闭的资源（Cursor、Stream、Connection）

### 2c. 输出建议

按优先级分类：

```
══ Code Review 建议 ══

CRITICAL（必须处理）
  1. [文件:行号] 描述...

HIGH（强烈建议）
  2. [文件:行号] 描述...

MEDIUM（建议改进）
  3. [文件:行号] 描述...

LOW（可选优化）
  4. [文件:行号] 描述...

共 N 条建议（CRITICAL: X, HIGH: X, MEDIUM: X, LOW: X）
```

如果没有发现问题，输出 "Code Review 通过，未发现需要改进的问题"。

### 2d. 门控

展示建议列表，询问用户：
```
请选择要采纳的建议（输入编号如 1,3,5 或 all 或 none）：
```

- 用户选择的建议：invoke `face-review-repair`，先读取完整上下文并判定
  `valid-bug` / `valid-quality` / `false-positive` / `already-fixed` /
  `pre-existing` / `uncertain`；只有 valid finding 才修改
- `mechanical` finding 可在针对性验证后自动应用；涉及控制流、事务、数据源、
  鉴权、API、配置、缓存/MQ、并发等语义风险时，必须先补保护行为的测试，
  禁止仅因改动少或来自 AI Review 就直接修改
- CRITICAL 级别不能跳过；用户拒绝修复时停止提交，不得 push 或创建 MR
- 应用修改后展示语义 diff 和测试证据供用户确认，并重新执行受影响的 lint/test

---

## Stage 3：人工校验文档生成

> 目标：生成详细的审查文档（流程图 + 架构图 + 关键代码），供人工最终 review

### 3a. 分析变更

读取所有变更文件（`git diff "origin/$TARGET_BRANCH"...HEAD`），分析：
- 修改了哪些类/方法/函数
- 涉及哪些业务模块
- 数据流方向和组件间调用关系
- 新增/删除/修改的公共 API

### 3b. 生成审查文档

文件路径：`docs/reviews/<branch-short-name>-review.md`

`<branch-short-name>` 取分支名最后一段，如 `bugfix/VH_2.92.0_20260310140519` → `VH_2.92.0_20260310140519`

文档内容：

```markdown
# Code Review 审查文档

> 分支：<当前分支>
> 目标：<目标分支>
> 生成时间：<YYYY-MM-DD HH:mm>

## 1. 变更概要

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| path/to/File.kt | 新增/修改/删除 | 一句话描述变更内容 |

共变更 N 个文件，新增 X 行，删除 Y 行。

## 2. 业务流程图

用 Mermaid flowchart 描述**变更涉及的完整业务流程**（不仅仅是变更部分，而是变更所在的完整流程上下文）。
用不同颜色标注变更的节点。

## 3. 架构图

用 Mermaid classDiagram 描述涉及的类/接口关系。
或用 sequenceDiagram 描述关键交互流程（如网络请求链路、事件传递链路）。

如果变更跨多个模块，展示模块间依赖关系。

## 4. 关键代码

列出最核心的代码片段（不超过 5 段），每段附注释说明：
- 这段代码的设计意图
- 为什么这样实现
- 需要关注的边界情况

## 5. 风险评估

| 风险项 | 等级(高/中/低) | 说明 | 缓解措施 |
|--------|---------------|------|---------|

## 6. 手动测试建议

- [ ] 正常场景: ...
- [ ] 异常场景: ...
- [ ] 边界场景: ...
- [ ] 回归场景: ...（验证未修改的相关功能是否受影响）
```

### 3c. 门控

```
审查文档已生成：docs/reviews/<branch>-review.md
请阅读后确认：人工 review 是否通过？
```

**此处必须等待用户明确确认后才能继续。** 这是最重要的人工校验点。

---

## Stage 4：智能暂存

> 目标：自动排除构建产物、本地配置和评审草稿，完整暂存需要提交的生产代码与测试代码

### 4a. 扫描变更

```bash
git status --porcelain
```

列出所有已修改、新增、删除的文件。

### 4b. 分类文件

对每个文件根据项目类型进行分类：

#### 自动排除（不暂存）

**通用排除**：
- `.idea/`, `*.iml`, `local.properties`, `.DS_Store`
- `build/`, `out/`, `dist/`, `target/`
- `docs/reviews/*-review.md`（Stage 3 文档仅供本地阅读，不提交）

**Android 额外排除**：
- `debugpanel/` 目录
- `.gradle/` 目录

**iOS 额外排除**：
- `DerivedData/`, `.build/`, `Pods/`
- `*.xcuserstate`, `*.xcuserdata`

**后端额外排除**：
- `venv/`, `node_modules/`, `__pycache__/`, `.pytest_cache/`

#### 需确认（内容检测可疑）

扫描待暂存的**生产代码**文件内容，标记以下可疑模式：

- 调试语句：`Log.d("test`, `print("debug`, `console.log("test`, `fmt.Println("debug`
- 硬编码测试 IP/URL：`192.168.`, `10.0.0.`, `http://localhost`（配置文件中的除外）
- 临时标记：`// TODO: remove`, `// FIXME`, `// HACK`, `// TEMP`, `// XXX`
- 调试开关：新增的 `BuildConfig.DEBUG` 条件、`#if DEBUG` 块、`if __debug__` 块

#### 正常暂存

以上规则未命中的文件。

新增和修改的测试代码必须正常暂存。测试是生产代码修复的验证证据，不得仅因为路径或
文件名包含 `test`、`Tests`、`androidTest` 或 `spec` 而排除。

### 4c. 展示暂存计划

```
══ 暂存计划 ══

将暂存（N 个文件）：
  + path/to/Feature.kt
  + path/to/FeatureTest.kt
  + path/to/layout.xml
  ...

将排除（M 个文件）：
  - docs/reviews/xxx-review.md（审查文档，仅供本地）
  ...

需确认（K 个文件）：
  ? path/to/Utils.kt — 第 42 行包含 Log.d("test data")
  ...

确认暂存？（对于"需确认"文件，可输入编号排除）
```

### 4d. 门控

用户确认后，逐个文件执行 `git add <file>`。**绝不使用 `git add -A` 或 `git add .`**。

---

## Stage 5：干净提交

> 目标：基于目标分支最新代码 rebase，生成干净的 commit

### 5a. Rebase 到最新目标分支

```bash
git fetch origin "$TARGET_BRANCH"
git rebase "origin/$TARGET_BRANCH"
```

**如果有冲突**：
1. 执行 `git diff --name-only --diff-filter=U` 列出冲突文件
2. 对每个冲突文件展示双方差异，建议保留策略
3. 辅助用户解决冲突
4. 用户确认后 `git add <resolved-file>` 然后 `git rebase --continue`
5. 如果用户希望放弃 rebase：`git rebase --abort`

**注意**：rebase 后不自动 force push，后续 Stage 6 统一推送。

### 5b. 生成 Changelog

```bash
git log "origin/$TARGET_BRANCH"..HEAD --oneline --no-merges
```

将 commit 按类型分组格式化：

```
## Changelog

### Features
- <commit message>

### Bug Fixes
- <commit message>

### Refactoring
- <commit message>

### Other
- <commit message>
```

如果只有一个 commit，changelog 就是该 commit 的描述。

### 5c. 构造 Commit Message

分析暂存的文件变更，生成 conventional commit message：

```
<type>(<scope>): <简洁描述>

<详细说明（可选，来自 changelog）>
```

- `type`：从变更内容推断（feat/fix/refactor/docs/test/chore/perf/ci/style）
- `scope`：从变更文件路径推断模块名
- 描述：一句话概括变更目的

### 5d. 门控

```
══ Commit 预览 ══

提交信息：
  feat(payment): add retry logic for Stripe payment

  - Add exponential backoff for failed payments
  - Handle network timeout gracefully

暂存文件（N 个）：
  M path/to/PaymentRetry.kt
  A path/to/RetryPolicy.kt
  ...

确认提交？（Y=提交 / edit=修改message / n=取消）
```

用户确认后执行：
```bash
git commit -m "<message>"
```

---

## Stage 6：MR 创建

> 目标：创建带完整文档的 MR，驱动到可合并

目标为 `main`、`master` 或 `release/*` 时，本 Stage 不得使用下方独立创建路径，
必须委托 `gitlab-mr`，由其先判定 `production-promotion` /
`production-non-promotion` / `emergency-hotfix`。需要绑定真实 MR HEAD 的 parity
check 在推送并创建或更新 MR 后、启动 Driver 前执行。禁止从 `code-submit` 绕过生产
晋级门禁。

### 6a. 推送分支

```bash
git push -u origin "$(git branch --show-current)" --force-with-lease
```

使用 `--force-with-lease`（因为 Stage 5 做了 rebase），比 `--force` 更安全。

### 6b. 准备 MR 文档

1. 检查 `docs/plans/` 或 `docs/04-user-stories/` 下是否有本次变更相关的文档
2. 如果没有：
   - 基于 Stage 3 审查文档的"变更概要"和"业务流程图"部分，创建精简版文档
   - 路径：`docs/plans/YYYY-MM-DD-<feature-name>.md`
   - 内容必须覆盖本次 MR 核心变更（CI 会检查相关性）
3. 提交并推送文档：
   ```bash
   git add docs/plans/<doc>.md
   git commit -m "docs: add <feature> design document"
   git push
   ```

### 6c. 创建 MR

构造 blob 链接：从 `git remote get-url origin` 解析 group/project，拼接分支名和文件路径。

```bash
glab mr create \
  --title "<Stage 5 的 commit 标题>" \
  --description "$(cat <<'EOF'
## Changelog

<Stage 5 生成的 changelog>

## 相关文档

- 设计文档：<GitLab blob 链接指向 docs/plans/ 下的文档>

## 变更类型

- [x] <对应类型：Bug fix / New feature / Refactoring / ...>
EOF
)" \
  --target-branch "$TARGET_BRANCH"
```

如果已有 MR，用 `glab mr update` 更新描述。

### 6d. CI 轮询（复用 gitlab-mr skill 逻辑）

```bash
sleep 5
glab ci status
```

轮询 pipeline 状态，处理常见失败：

| 失败 Job | 修复方式 |
|----------|---------|
| Merge conflict | `git fetch && git rebase && git push --force-with-lease` |
| 其他 | 展示失败 job 日志，提供修复建议 |

循环检查直到 CI 全绿 + 无冲突，或报告无法自动修复的问题。

### 6e. 完成报告

```
══ 提交完成 ══

MR: https://gitlab.addx.ai/xxx/project/-/merge_requests/XXX
CI: 通过 / 运行中 / 失败（详情...）
文档: <blob 链接>
Changelog:
  - feat: ...
  - fix: ...
```

---

## 注意事项

- `./gradlew` 和 `xcodebuild` 命令设置 `timeout: 300000`（5 分钟）
- 暂存时逐个文件 `git add`，**绝不使用 `git add -A` 或 `git add .`**
- Rebase 冲突和 force push 都需要用户确认
- Stage 3 的 review 文档仅供本地阅读，不提交到远程
- Stage 6 的 MR 文档是单独的精简版设计文档，会提交到远程
- blob 链接中的分支名和文件路径必须指向已推送的文件
- 使用 `--from=stageN` 跳过前面阶段时，跳过的阶段在最终报告中标注 "已跳过"
- 每个阶段的门控都必须等待用户明确确认，不能自动跳过

---

## Examples

### Good Example — 完整流程执行

```
用户: /code-submit --target=test/VH_2.92.0_20260310140519

AI: 项目类型：Android（依据：build.gradle + AndroidManifest.xml）

══ Stage 1: Lint 检查 ══
变更模块：:app, :BaseLib:payment
执行 ./gradlew :app:lint :BaseLib:payment:lint ...
Lint 检查结果：0 errors, 3 warnings
是否继续进入 Code Review？

用户: Y

══ Stage 2: Code Review ══
CRITICAL（必须处理）
  1. [PaymentRetry.kt:42] 未关闭的 Cursor — fetchPaymentHistory() 中 query 结果未 close
HIGH（强烈建议）
  2. [PaymentRetry.kt:78] 变量命名 — `d` 应改为 `retryDelay`
共 2 条建议
请选择要采纳的建议：

用户: all

[应用修改...]

══ Stage 3: 人工校验文档 ══
审查文档已生成：docs/reviews/VH_2.92.0_20260310140519-review.md
请阅读后确认：人工 review 是否通过？

用户: Y

══ Stage 4: 智能暂存 ══
将暂存（5 个文件）：
  + app/src/main/.../PaymentRetry.kt
  + app/src/main/.../RetryPolicy.kt
  ...
将排除（2 个文件）：
  - app/src/test/.../PaymentRetryTest.kt（测试文件）
  - docs/reviews/VH_2.92.0_20260310140519-review.md（审查文档）
确认暂存？

用户: Y

══ Stage 5: 干净提交 ══
提交信息：feat(payment): add retry logic for failed payments
确认提交？

用户: Y

══ Stage 6: MR 创建 ══
MR: https://gitlab.addx.ai/SWCLIEN/g0-android/-/merge_requests/456
CI: 通过
```

### Bad Example — Review 修改业务逻辑

```
# BAD: Code Review 阶段直接修改了用户的 if 条件逻辑
AI: 我已将 PaymentRetry.kt 第 35 行的 `if (retryCount < 3)` 改为 `if (retryCount < 5)`，
    因为 3 次重试可能不够。

# GOOD: 只提出建议，不直接修改
AI: MEDIUM
  3. [PaymentRetry.kt:35] 重试次数 — 当前 maxRetry=3，网络不稳定时可能不够，
     建议考虑是否调整。（注意：这是业务决策，仅供参考）
```

### Bad Example — 使用 git add -A 暂存

```
# BAD: 一次性暂存所有文件
git add -A
git commit -m "feat: add payment retry"

# GOOD: 逐个文件暂存，排除测试和调试文件
git add app/src/main/.../PaymentRetry.kt
git add app/src/main/.../RetryPolicy.kt
git commit -m "feat(payment): add retry logic for failed payments"
```
