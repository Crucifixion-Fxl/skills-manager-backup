---
name: a4x-logger-onboarding
description: Integrate or migrate a backend service to a4x-logger-sdk (unified structured logging with PII masking, IDTYPE tags, OTel trace correlation, and canonical request-scoped fields). Use when the user asks to "接入 a4x-logger-sdk", "迁移到 a4x-logger", "整合 logger sdk", "onboard XX to a4x-logger", or when a backend service still uses go-zero logx/logc, SLF4J without masking, or stdlib log and needs structured JSON output with PII compliance. Covers SDK init, ctx-bound logger middleware, IDTYPE struct tags, test rewrite, CI/Docker private-module auth, migration design doc, and code-review pushback patterns. Three-language SDK shipped — Go v0.2.2, Java 1.0.0, Python 1.0.0. SKILL routes by language after Phase 0 recon.
---

# a4x-logger-onboarding

## Description

新服务接入 `a4x-logger-sdk` 或存量服务迁移到统一日志 SDK 的标准流程。SDK 提供:

- **结构化 JSON 输出**(统一 schema,跨服务字段一致)
- **PII 脱敏**(正则 mask + IDTYPE 反射)
- **Canonical 字段**(user_id / request_id / device_msg_src / trace_id 等;`spec/log_format.json` 列 20 个,Go `With()` 白名单 14 个,Java MDC / Python `CANONICAL_KEYS` 12 个 — 同概念不同切片,以 `references/fields-and-idtype.md` 为准)
- **OTel trace 关联**(trace_id / span_id 自动从 ctx 抽取)
- **框架日志桥接**(go-zero logx / SLF4J / Python logging 自动路由到 SDK 管线)

**skill 设计理念**:教 **SDK 怎么正确用** + **怎么 recon 任意项目** + **接入流程纪律**。**不写"按源库 / 框架 / 项目结构分类"的转换手册** — 每个项目的 quirks 归在 case-study,不进 skill 主体。Agent 读 skill 学 SDK 用法,recon 实际项目,把 SDK 正确用法 apply 到 recon 出来的实际结构上。

### 触发场景

| 场景 | 用户说法 |
|---|---|
| 新服务从零接入 | "帮 X 服务接入 a4x-logger-sdk"、"X 要上日志 SDK" |
| 存量服务迁移 | "把 X 的 logx / stdlib log / 自研 wrapper / SLF4J 裸调用 / structlog 裸调用 / Python `logging` 直接调用 迁到 a4xlogger" |
| 多语言服务统一 | "我们的 Y 模块是 Go/Java/Python,也要接入" |

### 不在范围

- SDK 本身的 bug 修复(到 SDK 仓库 [CLOUD/a4x-logger-sdk](https://gitlab.addx.ai/CLOUD/a4x-logger-sdk))
- 日志采集链路(Loki / ELK 配置,委派 `log-ingestion-elasticsearch`)
- 产品级埋点(业务事件,委派 `observability-design`)

### 推荐版本

| 模块 | 当前推荐 tag | 形态 / 备注 |
|---|---|---|
| `gitlab.addx.ai/CLOUD/a4x-logger-sdk/go`(+ `/go/gozero`)| **`go/v0.2.2`** | go module |
| `com.a4x.logger:a4x-logger-starter`(+ `-core` / `-format`)| **`java/v1.0.0`**(Maven `1.0.0`)| Spring Boot starter,JDK 11+,Spring Boot 2.5.5–2.7.x |
| `a4x-logger` Python package | **`python/v1.0.0`** | 通过 `pip install git+https://...@python/v1.0.0` 或 PyPI 私服 |
| spec | 1.1.0 | 含 `placeholder_input_invariant` / null-omit |

权威版本矩阵 → SDK 仓 [`docs/architecture/version-matrix.md`](https://gitlab.addx.ai/CLOUD/a4x-logger-sdk/-/blob/master/docs/architecture/version-matrix.md)。

查最新 tag:`git ls-remote --tags https://gitlab.addx.ai/CLOUD/a4x-logger-sdk.git | grep -E '(go|java|python)/v'`

## Ground Rules — 两条 invariant,违反 = bug

### 规则 1:引用任何 SDK 能力前必须 verify

skill 里所有提到的 SDK API / 配置字段 / IDTYPE 值 / canonical 字段 / helper,**必须能在 SDK 仓库里找到对应代码或 spec**。Verify 不到 → STOP + 报告用户 + **不发明**。

**Phase 0 第 0 步:本地 SDK 源码准备**(三语公共)

1. 探已有副本:`Glob ~/projects/a4x-logger-sdk/.git` / `Glob ~/addx-*/a4x-logger-sdk/.git` / `Glob ~/code/a4x-logger-sdk/.git`
2. 探到 → **时效检查**:`git -C <sdk> log -1 --format=%cr` > 30 天 → 报告里提"本机副本 X 个月前,要不要 pull?"
3. 探不到 → 问用户:"我要 clone SDK 做 verify,放哪?建议 [a] `~/projects/a4x-logger-sdk` [b] `~/addx-java-project/a4x-logger-sdk` [c] `/tmp/a4x-logger-sdk-ref`(重启丢) [d] 自定义"
4. 用户给路径 → `git clone https://gitlab.addx.ai/CLOUD/a4x-logger-sdk.git <path>`(三语同一 monorepo,一次 clone 全覆盖)
5. 项目装特定 tag(`go/v0.2.2` / `java/v1.0.0` / `python/v1.0.0`)→ `git -C <sdk> fetch --depth 1 origin tag <tag> && git -C <sdk> checkout <tag>`
6. clone 失败:网络问题让用户排查;鉴权 401/403 问"SSH key / token 没配?"

路径记为 `<sdk>`,后续整个任务用它做 verify 入口。

**包名 → 目录映射 + Verify 入口** 已下沉到对应语言文档:

| 语言 | 入口 |
|---|---|
| Go | [references/go/sdk-usage.md §0](references/go/sdk-usage.md) |
| Java | [references/java/sdk-usage.md §0](references/java/sdk-usage.md) |
| Python | [references/python/sdk-usage.md §0](references/python/sdk-usage.md) |

### 规则 2:回答和改造都必须符合 SDK 实际能力

写给用户的代码 / 配置 / 文档全部受规则 1 约束。**项目层 convention**(如 `action=X result=Y` 命名)不需要 verify SDK,但**必须明确标注"非 SDK 强制,项目层软约定"**,避免读者误以为 SDK 会校验。

详见 [references/go/sdk-usage.md §11](references/go/sdk-usage.md) "项目层 convention vs SDK 硬规则"。

---

## 执行流程

### Phase 0 — Recon(Agent 自己探查 + 1-shot 报告确认)

1. **SDK 源码准备**(Ground Rule §1 Phase 0 第 0 步)
2. **探目标项目 7 维度**:语言 / 框架 / 项目现状(检测任意日志库,不只 logx)/ CI / 本地起法 / middleware 栈 / auth 机制 / SDK 现状版本
3. **1-shot 报告输出**:按模式(全新接入 vs 已有日志迁移)填写计划 + 留空字段标 `<?>`
4. **等用户确认**,有纠错即吸收再执行

**详细 grep recipes + 报告模板**:[project-recon.md](references/project-recon.md)

### Phase 1 — 执行接入

按 Phase 0 recon 出来的语言走对应 execution.md:

| 语言 | 入口 | 骨架步数 | 主要要点 |
|---|---|---|---|
| Go | [references/go/execution.md](references/go/execution.md) | 11 步 | logx adapter 接线 + svcCtx 持 logger + LoggerMiddleware |
| Java | [references/java/execution.md](references/java/execution.md) | 7 步 | Spring Boot starter 自动装配 + MDC 写入方决策 + Filter 顺序 |
| Python | [references/python/execution.md](references/python/execution.md) | 7 步 | `setup_logging` + 业务 ASGI/WSGI middleware + contextvars 写入 |

三语共用 "每一步必须证明两件事":
1. **SDK 接入功能生效**(logger init / 字段注入 / IDTYPE 渲染 / middleware 挂上)
2. **业务无回归**(`<lang-test-cmd>` 失败数 ≤ Step 0 baseline + 本步涉及 domain 的 smoke diff 为空)

缺任何一件 STOP 排查。**改日志的 MR 能破坏业务是反直觉但高频的事**。详见各语言 `common-pitfalls.md` + shared [verification-checklist.md](references/verification-checklist.md)。

---

## 引用资源

**📘 SDK 用法 + 接入流程(按语言)**

- Go:[references/go/sdk-usage.md](references/go/sdk-usage.md) · [execution.md](references/go/execution.md) · [common-pitfalls.md](references/go/common-pitfalls.md) · [ci-docker-template.md](references/go/ci-docker-template.md) · [pii-scanner.md](references/go/pii-scanner.md)
- Java:[references/java/sdk-usage.md](references/java/sdk-usage.md) · [execution.md](references/java/execution.md) · [common-pitfalls.md](references/java/common-pitfalls.md) · [ci-docker-template.md](references/java/ci-docker-template.md) · [pii-scanner.md](references/java/pii-scanner.md)
- Python:[references/python/sdk-usage.md](references/python/sdk-usage.md) · [execution.md](references/python/execution.md) · [common-pitfalls.md](references/python/common-pitfalls.md) · [ci-docker-template.md](references/python/ci-docker-template.md) · [pii-scanner.md](references/python/pii-scanner.md)

**📕 跨语言概念契约(shared)**

| 文档 | 用途 |
|---|---|
| [fields-and-idtype.md](references/fields-and-idtype.md) | canonical 字段表 + IDTYPE 变体,每条 inline 3 语言写法(具体字段数与变体数以 `spec/log_format.json` 为准)|
| [log-style-guide.md](references/log-style-guide.md) | 级别 / 错误日志 / 消息模式,每条 inline 3 语言示例 |

**📗 通用工具(shared)**

| 文档 | 用途 |
|---|---|
| [project-recon.md](references/project-recon.md) | 7 维度通用 grep recipes + 三语检测命令 + Phase 1 dispatch |
| [regression-smoke-template.md](references/regression-smoke-template.md) | Agent 4 阶段生成定制 smoke,含 go/mvn/pytest 子段 |
| [verification-checklist.md](references/verification-checklist.md) | Step 0 baseline + 每步双验证,命令分语言 |
| [review-pushback-templates.md](references/review-pushback-templates.md) | Sonar / AI review 常见 finding 回复模板(三语)|

**📒 真实案例**

| 文档 | 用途 |
|---|---|
| [case-studies/naturehood.md](references/case-studies/naturehood.md) | naturehood API(Go + go-zero)真实接入 — production case-study |
| [case-studies/java-spring-demo.md](references/case-studies/java-spring-demo.md) | Java Spring Boot baseline(demo-driven,基于 SDK 仓 examples)|
| [case-studies/python-fastapi-demo.md](references/case-studies/python-fastapi-demo.md) | Python FastAPI baseline(demo-driven,基于 SDK 仓 examples)|

**case-studies 用法**:看真实案例理解上下文,**不要照抄具体数字 / 函数名 / batch 数**。每个新接入的服务(尤其第一个 Java/Python 生产接入)应在 `case-studies/` 下加一份 production case study,**而不是回头改 skill 本体**。

---

## Examples

### Good — SDK 正确用法(每语言一个 minimal 正例)

**Go**(go-zero):

```go
logger := a4xlogger.New("my-svc", a4xlogger.WithConfigFile("etc/logger.yaml"))
defer logger.Sync()
logx.SetWriter(a4xgozero.NewWriter(logger))   // 必须在 MustNewServer 之后

l := a4xlogger.FromContext(ctx)
l.Info("get_user user_id=%v", req.UserID)     // IDTYPE 反射 + canonical 注入
```

**Java**(Spring Boot):

```java
// pom.xml: com.a4x.logger:a4x-logger-starter:1.0.0
// application.yml: a4x-logger.service-name + sensitive masks
private static final Logger log = LoggerFactory.getLogger(UserService.class);

log.info("查询用户 {}", user);   // user 是 POJO,@SensitiveField 字段自动 IDTYPE 包装
```

**Python**(FastAPI):

```python
from a4x_logger import setup_logging
import structlog

setup_logging("user-service", config_file="logger.yaml")
# 业务 ASGI middleware 在请求入口 bind_contextvars(user_id=..., request_id=...)

logger = structlog.get_logger()
logger.info("查询用户 %s", user_id)            # %s 占位符,SDK 内部替换
```

### Bad — 高频反面(完整清单见各语言 common-pitfalls.md)

**Go**(高频四坑):

```go
// ❌ 1. 前置 middleware 用 FromContext → Nop 静默吞日志
log := a4xlogger.FromContext(r.Context())  // 零输出
// ❌ 2. fmt.Sprintf 绕反射 → PII 明文落盘
log.Info(fmt.Sprintf("user %v", user))
// ❌ 3. logx.SetWriter 顺序反 → adapter 被 MustNewServer 覆盖
// ❌ 4. 业务 Logic 直接用 logx.Xxx(应该用 FromContext(ctx).Info)
//      logx.Infow struct 参数下 IDTYPE 反射有效但 canonical 不注入;Infof printf 路径 IDTYPE 失效
```

**Java**(高频两坑):

```java
// ❌ 1. MDC.put 没在 finally 里 clear → Tomcat 线程复用,下一请求继承上一请求的 user_id
MDC.put("user_id", uid);
// ... 业务可能抛异常被全局 handler 吞掉
// 没 finally { MDC.clear() } → 线程 #5 被 reuse 时 user_id 仍是上个请求的

// ❌ 2. 字符串拼接打 PII → SDK Format 层拿不到原始对象,反射失效
log.info("user " + user.toString());           // 改成 log.info("user {}", user);
```

**Python**(高频两坑):

```python
# ❌ 1. f-string / .format() / + 拼接 PII → SDK 反射拿不到原始对象
logger.info(f"user {user}")
logger.info("user {}".format(user))
logger.info("user " + str(user))
# 改成
logger.info("user %s", user)

# ❌ 2. structlog 和 stdlib logging 双管线:业务用 logging.info(...) 绕过 structlog 处理链
import logging
logging.info("...")    # JSON shape 不一致,trace_id 不注入
# 改成 structlog.get_logger().info("...")
```

完整反例和事故场景详见各语言:[Go](references/go/common-pitfalls.md) / [Java](references/java/common-pitfalls.md) / [Python](references/python/common-pitfalls.md)。
