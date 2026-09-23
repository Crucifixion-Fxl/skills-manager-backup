# Code Review 推回模板

接入 MR 常见 review finding 的标准回复。**原则**:不是每条 review 建议都要改,要区分真问题 vs 风格偏好 vs 超出范围。推回时必须给出**合规的理由**,不要硬刚。

## 分类框架

| 类型 | 是否改 | 理由 |
|---|---|---|
| 真 bug / 安全风险 | ✅ 改 | 代码不对 |
| 真改进 / 本 MR 范围内 | ✅ 改 | 顺手的事 |
| 项目级 tech debt,非本 MR 引入 | ❌ 推回 | 本 MR scope 不背这个 |
| 风格偏好,与项目现有惯例冲突 | ❌ 推回 | 单改让代码更不一致 |
| reviewer 自己没看完上下文 | ❌ 推回 | 提供上下文 |

---

## 1. Sonar:Cognitive Complexity(预存在的 tech debt)

**场景**:接入 SDK 给某业务函数(如 `NewServiceContext`)只加了 1 行 field 赋值或参数,Sonar 把整个函数的既有复杂度 27 算到 MR 头上。

**推回模板**:

```markdown
**关于 Sonar S3776(`<funcName>` Cognitive Complexity 27 > 15)**

本 MR 在 `<funcName>` 内仅新增 <具体改动,如"1 行 Logger 字段赋值 + 1 个函数参数">,未实质增加函数复杂度。函数原复杂度已为 27(pre-existing tech debt),与本次 SDK 接入范围无关。

Sonar 把这函数标为 "new code" 是因为本 MR 改动了它的行(签名 / 单行字段赋值),但这属于**最小侵入式注入**,重构该函数应独立 ticket 处理。

建议:单独开 ticket 处理 `<funcName>` 复杂度重构,本 MR 范围严格限定在 "SDK 接入",不改业务代码。
```

**如果 Sonar 仍不 dismiss,降级到 `//NOSONAR` 抑制**:

在 `<funcName>` 签名行末加注释:
```go
func <funcName>(...) *<Type> { //NOSONAR S3776: pre-existing complexity 27>15, not introduced by this MR — flagged as "new code" only because the signature was touched.
```

---

## 2. Sonar:literal 重复(可改 — 小范围工作量)

**场景**:测试文件里某字符串重复 N 次,Sonar 建议提取常量。

**判断**:
- **< 4 次重复** → 推回(Sonar 规则本身默认 4 起,少于不罚)
- **≥ 4 次,都在同一个测试文件** → **可改**,加 const 块,一次性处理
- **跨文件** → 推回(项目级 tech debt)

**可改的话,提取模板**:

```go
// 在 test 文件开头或 _test.go 的 init 块里
const (
    resultSuccess         = "result=success"
    errMsgExpectedSuccess = "expected result=success, got:\n%s"
)

// 原来:
if !strings.Contains(msg, "result=success") { ... }
// 改成:
if !strings.Contains(msg, resultSuccess) { ... }
```

---

## 3. AI code review:`sensitive.on-error` 建议 fail-close

**场景**:AI reviewer 质疑 `logger.yaml` 的 `on-error: log_raw` 策略,建议改 `drop_message`(脱敏失败时丢弃整条日志)。

**推回模板**(保持 log_raw):

```markdown
**关于 AI review P1-2(`sensitive.on-error: log_raw`)**

保持 `log_raw`(SDK 默认值)。理由:

1. **脱敏失败本身属于 SDK 内部异常**(正则实现问题),不会在正常业务路径触发
2. **失败可观测**:SDK 内部 metrics / 告警可发现 mask 失败,无需靠 fail-close 兜底
3. **`drop_message` 代价更高**:会导致关键业务日志丢失,故障排查困难
4. **现有 mask 规则**已覆盖 phone / email 等主要 PII 字段,本 MR 中已补全 key-triggered 正则

后续若出现 false-negative,将通过**增加 pattern** 而非切换 fail-close 策略解决。
```

**如果业务有强合规要求**(比如金融 / 医疗),**要改**。默认场景按上面模板推回。

---

## 4. AI code review:文档缺失("代码有文档无")

**场景**:AI reviewer 标红线 P0 说代码大改但无迁移设计文档。

**处理**:**必改**,不要推回。这是合理要求。

按 [execution.md §6.1 migration doc 模板](execution.md) 补 `docs/architecture/logging/<service>-a4x-logger-migration.md`。

---

## 5. AI code review:CI_JOB_TOKEN 暴露面收敛

**场景**:AI reviewer 指出 Dockerfile 里 `git config --global` 不在同一个 RUN cleanup,或用了 `set -x` 回显 token。

**处理**:**必改**。这是真安全问题,不推回。

按 [ci-docker-template.md](ci-docker-template.md) 的模式:
- 同层 RUN 里 use + cleanup
- credential-bearing RUN 不用 `-x`
- 加 `dockerfile-credential-check` CI job 防回归

---

## 6. Reviewer 说某业务代码"顺手重构一下"

**场景**:接入 MR 里 reviewer 看到某函数风格不好,建议顺手改。

**推回模板**:

```markdown
本 MR 的 scope 是接入 a4x-logger-sdk,不改业务代码(见 MR 描述 "Non-goals")。

您提的 `<funcName>` <issue description> 是个好建议,但:

1. 范围跟本 MR 不相关(接入 SDK 不触发这个函数的重写需求)
2. 混进来会让 MR diff 变大,review 成本 + 回滚复杂度都上升
3. 本次接入有 staging 观察期,混业务改动会模糊 "log 变化" vs "业务 bug" 的归因

建议单开 ticket 做 `<funcName>` 重构。本 MR 合并后,请在 `<tracker>` 开个 issue,我 link 过来。
```

---

## 7. Reviewer 说"加个 try/catch / error handling"

**场景**:reviewer 建议在 `loggerMiddleware.Handle` 里加 recover / error swallow。

**推回模板**:

```markdown
不加。SDK 已在 ADR-15 "永不 panic 穿透业务" 合同内提供 recover 兜底 —— 任何 SDK 内部 panic 都会:
1. 内部 recover + 输出带 `internal_error` 字段的日志
2. 业务 goroutine 继续运行

在 middleware 再加一层 recover = 重复防御,且会**掩盖 SDK 内部错误** —— SDK 本想让 `internal_error` 字段暴露出来便于排查,middleware 再兜会让它再也看不见。

如果未来 SDK 自身 recover 出问题,应该在 SDK 侧修,不在调用方堆兜底。
```

---

## 通用回复注意事项

1. **引用规则编号**(S3776 / go:S1192 / P1-2)让回复可审计,不要说 "Sonar 那条"
2. **给出数据**(比如"项目中该 pattern 已有 <N> 处")让推回不显得主观
3. **给替代方案**(建议单独 ticket),让 reviewer 感觉问题被 track 住,而不是被忽视
4. **不争论风格偏好**:如果 reviewer 就是坚持某风格,要么屈服改一下,要么升级到 tech lead 决断,不要在 MR 评论区长帖
5. **修正态度**:推回不是对抗,而是在说"我同意问题存在,但这个 MR 不是解决它的地方",语气尽量平衡

---

## Java Sonar 常见 finding

### J1. `java:S1192` — 字符串字面量重复

**现象**:测试文件或业务代码里某字符串(如日志 action name / message fragment)重复 ≥ 4 次,Sonar 建议提取 constant。

**判断与模板**:

- **< 4 次重复** → 推回:

  ```markdown
  **关于 Sonar java:S1192(`"<literal>"` 重复 <N> 次)**

  Sonar 规则默认触发阈值为 4 次,此处仅 <N> 次,未超阈值。本 MR 在迁移日志时
  保留了原 message 文本(见 execution.md §4 规则②"message 文本基本保留"),
  不在迁移阶段引入 constant 重构。

  建议:单开 ticket,统一提取全服务公共常量。
  ```

- **≥ 4 次,都在同一测试文件** → **可改**,在该测试文件开头加 `private static final String`:

  ```java
  private static final String RESULT_SUCCESS = "result=success";
  // 替换所有硬编码字符串
  ```

- **跨文件** → 推回(项目级 tech debt,非本 MR scope)。

---

### J2. `java:S2629` — SLF4J 日志模板未使用占位符

**现象**:Sonar 检测到 `log.info("user " + uid)` / `log.info(String.format("user %s", uid))` 等字符串拼接形式,报 S2629 要求改用 `{}` 占位符。

**处理**:**必改**,这是本 MR 接入 a4x-logger 的核心纠错内容。这正是 execution.md §4 规则③的机械替换目标。

**回复模板**(若 reviewer 问"为什么 MR 里还有未改的"):

```markdown
**关于 Sonar java:S2629 — 本 MR 尚未覆盖的拼接写法**

本次接入按 execution.md §4 分批策略处理,batch <N> 已覆盖 `<package>` 下的 <K> 处,
剩余 <M> 处分布在 `<other-package>`,计划在 batch <N+1> 的 commit 里完成。

若 Sonar 把未改的标红影响 MR 审核,可以:
1. 等 batch <N+1> commit 推上来(本 MR 下一个 commit 已在 draft)。
2. 或在该文件加 `//NOSONAR S2629: will be migrated in batch <N+1>` 临时抑制。
```

**不应该发生**的情况:如果当前 MR 已声称"Step 4 全量完成",那 S2629 不应有任何命中——排查是否漏改了某个 package。

---

### J3. `java:S3776` — Cognitive Complexity 超阈

见 [§1 Sonar:Cognitive Complexity(预存在的 tech debt)](#1-sonarCognitive-Complexity预存在的-tech-debt)。

此处补充 Java 接入场景专用的判断:
- 若触发函数是 `ServiceContext` 构造函数 / `configure` 方法:大概率是"SDK init 加 1 行赋值触发了 Sonar 把该函数标为 new code"。直接套 §1 推回模板。
- 若触发函数是业务 Logic / Service 方法:本 MR 不应该改这些函数逻辑,scope 外。同套 §1 推回模板。

---

## Python Sonar 常见 finding

### P1. `pythonsecurity:S5145` — 路径拼接 / 文件路径注入

**现象**:Sonar 检测到代码里用用户输入拼接文件路径(如 `open(base_dir + user_input)`)而没有 sanitize,标 `pythonsecurity:S5145`。

**背景**:SDK 仓 Python commit `bc852ef` 中实战遭遇过这条 finding,在 CI 里触发了 SAST 告警。

**处理**:
- **如果是 SDK 接入改动引入的路径拼接** → **必改**:
  ```python
  # ❌ 触发 S5145
  with open(log_dir + "/" + filename, "w") as f: ...

  # ✅ 使用 pathlib + 明确 base_dir 约束
  import pathlib
  log_path = pathlib.Path(log_dir).resolve() / filename
  # 如有必要,验证 log_path 在预期目录内
  assert log_path.parent == pathlib.Path(log_dir).resolve()
  ```
- **如果是业务已有代码、非本 MR 引入** → 推回:

  ```markdown
  **关于 Sonar pythonsecurity:S5145(`<file>:<line>`)**

  此处路径拼接在本 MR 之前已存在(见 git blame:commit <hash>),本次日志迁移
  未触碰该代码路径。本 MR scope 是接入 a4x-logger SDK,不改业务文件 I/O 逻辑。

  建议:单开 security ticket 处理路径注入加固,参考 pathlib.Path 用法。
  ```

---

### P2. `python:S1481` / `flake8 F841` — 未使用变量

**现象**:迁移时把 `logger.info("msg " + str(u))` 改成 `logger.info("msg %s", u)` 后,若原来有 `msg = "msg " + str(u)` 中间变量,改完后 `msg` 成了未使用变量。

**处理**:**必改**,删掉未使用的中间变量。这是本 MR 迁移过程中引入的,不是 pre-existing tech debt。

```python
# ❌ 迁移后遗留未使用变量
msg = "user " + str(u)  # F841: msg defined but never used
logger.info("user %s", u)

# ✅ 直接用占位符,删掉中间变量
logger.info("user %s", u)
```

---

### P3. `python:S5659` / Bandit B105/B106 — hardcoded credentials / token

**现象**:测试文件里有 `api_key = "test-token-123"` 这类 hardcoded 字符串用于单测,SAST 报 potential credential。

**处理**:
- **测试文件里的假 token / mock key** → 推回,并说明:

  ```markdown
  **关于 Bandit B105 / python:S5659(`tests/<file>:<line>`)**

  该 "credential" 是测试专用 mock 值(`"test-token-123"` / `"fake-api-key"`),
  不对应任何真实系统。参见:
  - 文件在 `tests/` 目录,不会打包进生产镜像
  - 值为 hardcoded fake,不符合任何真实 token 格式

  建议:若 Sonar 仍无法 dismiss,在该行加注释:
  `# noqa: S105` 或 `# nosec B105`
  ```

- **生产代码里的 hardcoded token** → **必改**,换成环境变量 / secrets manager。

---

## 留证据(给本 skill 持续改进用)

如果发现本 skill 没覆盖到的 review 模式,在本文件加一条,下次用户接入就有参考。
