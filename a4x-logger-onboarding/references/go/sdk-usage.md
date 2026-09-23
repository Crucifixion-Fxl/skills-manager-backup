# SDK Usage — Go

> 本文件教 **`a4x-logger-sdk/go` 怎么正确用**(API 入口 / logger 源 / IDTYPE 反射 / 禁用写法 / SDK 行为承诺)。不教怎么 recon 项目(看 [project-recon.md](project-recon.md))也不教接入流程(看 [execution.md](execution.md))。
>
> **基准版本**:v0.2.2(`NewRequestID` 引入 + gozero adapter caller dedup 修复)。
>
> **SDK 权威文档在** [a4x-logger-sdk/docs/architecture/go/usage.md](https://gitlab.addx.ai/CLOUD/a4x-logger-sdk/-/blob/master/docs/architecture/go/usage.md)。本文件是 **skill 侧精简版** + **每条内容都标注 verify 入口**(agent 引用前必须能在 SDK 仓库 grep 到)。

---

## 1. API 入口

### 1.1 核心 API(全部来自 `gitlab.addx.ai/CLOUD/a4x-logger-sdk/go`)

| API | 签名 | 用途 | Verify |
|---|---|---|---|
| `New(service string, opts ...Option) *Logger` | 构造 base logger,读 `logger.yaml`,永不返回 nil | main 入口唯一调用点 | `Grep '^func New\(' <sdk>/go/logger.go` |
| `FromContext(ctx) *Logger` | 从 ctx 取 bound logger;没绑返回 `Nop().WithContext(ctx)` | **LoggerMiddleware 之后**的业务代码 | `Grep '^func FromContext\b' <sdk>/go/` |
| `WithLogger(ctx, logger) ctx` | 把 logger 塞进 ctx(LoggerMiddleware 内部用) | 一般不直接调 | `Grep '^func WithLogger\b' <sdk>/go/` |
| `NewFromZap(*zap.Logger) *Logger` | 把现成 zap logger 包成 SDK logger(测试用) | `testutil.NewLogCapture` 内部 | `Grep '^func NewFromZap\b' <sdk>/go/` |
| `Nop() *Logger` | 静默 logger,所有方法 no-op(**陷阱,见 §3**) | 默认 ctx 没绑时的 fallback | `Grep '^func Nop\b' <sdk>/go/` |
| `NewRequestID() string` | 生成 32 hex 无连字符 request ID | LoggerMiddleware 补 X-Request-Id header 缺失时 | `Grep '^func NewRequestID\b' <sdk>/go/` |

### 1.2 Logger 方法

| 方法 | 签名 | 说明 |
|---|---|---|
| `.Info(msg, args...)` | printf style,支持 `zap.Field` 纯 field mode | 自动检测 args 全 zap.Field → zap 路径,否则 printf |
| `.Warn` / `.Error` / `.Debug` | 同上 | 同上 |
| `.Infof` / `.Warnf` / `.Errorf` / `.Debugf` | printf style 别名 | 跟 `.Info` 等完全等价(SDK v0.2.2 起) |
| `.With(fields...) *Logger` | 返回绑了额外 zap.Field 的新 logger | LoggerMiddleware 里用来绑 canonical 字段 |
| `.WithContext(ctx) *Logger` | 返回挂了 ctx 的 logger(emit 时自动抽 OTel trace_id) | **前置 middleware 必用** |
| `.Sync()` | flush 到底层 writer | `main` 里 `defer logger.Sync()` |
| `.Unwrap() *zap.Logger` | 拿原始 zap logger(一般不用) | 特殊场景才用 |

Verify:`Grep '^func \(.*\*Logger\) ' <sdk>/go/logger.go`

### 1.3 go-zero adapter(仅 go-zero 项目)

`gitlab.addx.ai/CLOUD/a4x-logger-sdk/go/gozero`:

| API | 用途 |
|---|---|
| `NewWriter(logger *a4xlogger.Logger) logx.Writer` | 把 go-zero logx 框架日志路由到 SDK 管线 |

Verify:`Grep '^func NewWriter\b' <sdk>/go/gozero/`

---

## 2. 取 Logger 的 3 种源(按调用点选)

**这是 SDK 使用里最容易踩的选择题**。选错结果:要么静默吞日志(Nop 陷阱),要么 trace_id 丢失。

| 调用点 | 取 logger 的方式 | 例子 |
|---|---|---|
| **有 `r.Context()` / `ctx`,且位于 LoggerMiddleware 之后**(HTTP handler / Logic struct / 工具函数) | `a4xlogger.FromContext(ctx)` | Logic 构造函数里 `logger: a4xlogger.FromContext(ctx)` |
| **LoggerMiddleware 之前跑的 middleware 内** / **任何 middleware Handle 函数内** | middleware struct 持 `base *a4xlogger.Logger` 字段,Handle 里 `m.logger.WithContext(r.Context())` | recovery / UserId / Tenant / authctx 等前置 mw |
| **启动期 / main / bootstrap 直接函数**(`main()` 体内)| 直接用 `main` 里 `a4xlogger.New()` 返回的 base logger | `logger.Error("config_load failed", err); os.Exit(1)` |
| **中间层 ctxless 函数**(e.g. `BuildMux()` / `NewJob()` / `InitXxx()` — 调用方还有 ctx 但函数自己没接)| **推荐改签名加 `ctx context.Context` 为首参**,函数内用 `a4xlogger.FromContext(ctx)` 或 `base.WithContext(ctx)`。加 ctx 是 idiomatic Go,便于 trace 传播 | `func BuildMux(ctx context.Context, ...) *http.ServeMux { log := a4xlogger.FromContext(ctx); ... }` |
| **后台任务/ 定时任务(有自己的 ctx,但不是 HTTP ctx)**| `baseLogger.WithContext(taskCtx).With(zap.String("task_name", ...))` | daily 任务 / asynq worker handler |

**中间层函数迁移的决策优先级**:
1. **首选**:改签名加 `ctx context.Context` 作首参,调用方把 ctx 传下来(即使当前没用也建好 pipeline)
2. **次选**:改签名加 `logger *a4xlogger.Logger` 参数(当函数确实与任何 ctx 无关,如纯 init-time 配置组装)
3. **禁用**:包级 global logger 变量(违反 SDK 设计意图,且 trace_id 无法传播)

### 为什么不能全用 `FromContext`

LoggerMiddleware 之前的 ctx 里**没绑 logger**,`FromContext(ctx)` 返回 `Nop()` — **静默吞所有日志**,不报错不警告。典型踩坑点是 recovery middleware 或 auth middleware 调 `FromContext` 想打警告,线上排障才发现日志消失。

详细时序图见 [common-pitfalls.md #1](common-pitfalls.md) + [SDK usage.md 附录 A.1-A.2](https://gitlab.addx.ai/CLOUD/a4x-logger-sdk/-/blob/master/docs/architecture/go/usage.md#appendix-a)。

---

## 3. 主入口初始化模板

```go
import (
    a4xlogger "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go"
    a4xgozero "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go/gozero"
    "github.com/zeromicro/go-zero/core/logx"
    "github.com/zeromicro/go-zero/rest"
)

func main() {
    flag.Parse()
    var c config.Config
    conf.MustLoad(*configFile, &c, conf.UseEnv())

    server := rest.MustNewServer(c.RestConf)   // ← 顺序:先 server
    defer server.Stop()

    logger := a4xlogger.New("<service-name>",   // ← 再 SDK init
        a4xlogger.WithConfigFile("etc/logger.yaml"),
    )
    defer logger.Sync()

    logx.SetWriter(a4xgozero.NewWriter(logger))  // ← 最后 adapter(go-zero 项目)

    ctx := svc.NewServiceContext(c, logger)
    handler.RegisterHandlers(server, ctx)
    server.Start()
}
```

**关键顺序约束**:`logx.SetWriter()` **必须在** `rest.MustNewServer()` **之后**(见 [common-pitfalls.md #3](common-pitfalls.md) — MustNewServer 内部会重置 logx writer)。

**logger.yaml** 最小模板:

```yaml
a4x-logger:
  level: INFO
  max-message-length: 32768
  sensitive:
    enabled: true
    on-error: log_raw  # 默认,脱敏失败保留原文 + metrics 兜底
    masks:
      phone:
        - 'phone[:=]\s*([^,\s"]+)'
      email:
        - 'email[:=]\s*([^,\s"]+)'
```

Verify:`Read <sdk>/go/sensitive/mask.go`(`MaskConfig` struct 的 `yaml:` tag)或 SDK usage.md §7。

---

## 4. LoggerMiddleware 完整模板

中间件职责:从 ctx 收集 canonical 字段 → 绑到 logger → 写回 ctx,供后续业务代码通过 `FromContext(ctx)` 使用。

```go
package middleware

import (
    "net/http"

    "go.uber.org/zap"

    a4xlogger "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go"
)

type LoggerMiddleware struct {
    base *a4xlogger.Logger
}

func NewLoggerMiddleware(base *a4xlogger.Logger) *LoggerMiddleware {
    return &LoggerMiddleware{base: base}
}

func (m *LoggerMiddleware) Handle(next http.HandlerFunc) http.HandlerFunc {
    return func(w http.ResponseWriter, r *http.Request) {
        ctx := r.Context()
        var fields []zap.Field

        // user_id:仅项目有 auth(JWT/session)时启用。无 auth 项目整块留 TODO 注释掉
        if uid := <project>.UserIDFromCtx(ctx); uid != 0 {
            fields = append(fields, zap.Int64("user_id", uid))
        }

        // request_id:header 有透传,无则 SDK 生成 32 hex(无连字符)
        if rid := r.Header.Get("X-Request-Id"); rid != "" {
            fields = append(fields, zap.String("request_id", rid))
        } else {
            fields = append(fields, zap.String("request_id", a4xlogger.NewRequestID()))
        }

        // device_msg_src:服务端请求写死 "cloud"
        fields = append(fields, zap.String("device_msg_src", "cloud"))

        reqLogger := m.base.With(fields...)
        ctx = a4xlogger.WithLogger(ctx, reqLogger)
        next(w, r.WithContext(ctx))
    }
}
```

**ServiceContext 接线**(go-zero):

```go
type ServiceContext struct {
    Config config.Config
    Logger rest.Middleware   // ← 加这字段
    // ... 原有字段
}

func NewServiceContext(c config.Config, logger *a4xlogger.Logger) *ServiceContext {
    return &ServiceContext{
        Config: c,
        Logger: middleware.NewLoggerMiddleware(logger).Handle,
    }
}
```

路由注册时把 `ctx.Logger` 挂到所有业务路由组(见 [execution.md](execution.md) Step 3)。

### 4.1 不同框架的挂载签名

主模板的 `Handle(next http.HandlerFunc) http.HandlerFunc` 是 **go-zero rest** 的签名。其他 Go HTTP 框架(net/http.ServeMux / Gin / Chi 等)签名不同,LoggerMiddleware 要提供对应的方法:

| 框架 | 挂载方法签名 | 注册方式 |
|---|---|---|
| go-zero rest | `Handle(next http.HandlerFunc) http.HandlerFunc` | `rest.WithMiddlewares([m.Handle], ...)` |
| net/http.ServeMux | `Wrap(next http.Handler) http.Handler` | `wrappedMux := m.Wrap(mux)` |
| Gin | `Gin() gin.HandlerFunc` | `engine.Use(m.Gin())` |
| Chi | 同 `Wrap`(Chi 用 `http.Handler`) | `r.Use(m.Wrap)` |

**同一 LoggerMiddleware struct 可提供多个方法**,内部字段注入逻辑抽一个私有 helper 复用:

```go
// 私有 helper:所有挂载方法共用
func (m *LoggerMiddleware) inject(ctx context.Context, header http.Header) context.Context {
    var fields []zap.Field
    // ... user_id / request_id / device_msg_src 注入逻辑 ...
    reqLogger := m.base.With(fields...)
    return a4xlogger.WithLogger(ctx, reqLogger)
}

// go-zero rest
func (m *LoggerMiddleware) Handle(next http.HandlerFunc) http.HandlerFunc { ... m.inject(...) ... }

// net/http.ServeMux / Chi
func (m *LoggerMiddleware) Wrap(next http.Handler) http.Handler { ... m.inject(...) ... }
```

**混合项目**:如果一个项目里既有 go-zero rest 又有 net/http.ServeMux(或其他框架)路由,**两边都要挂**本 middleware,不能只挂一边。具体怎么接到项目里看项目的服务器构造代码(`cmd/**/main.go` / `serverapp/*.go` 之类),project-recon.md 已教怎么找。

---

## 5. Canonical 字段清单(14 个 `.With()` 白名单 / 20 个 `filterReservedFields` reserved)

两个相关但独立的机制:
- **`.With(key, val)` 白名单 = 14 个**(`canonicalWithWhitelist` in `logformat/canonical_core.go`):`.With()` 只接受这 14 个 canonical key,传其他名字直接被忽略。
- **`filterReservedFields` reserved = 20 个**(`sdkReservedFields` in `logger.go:300`):工作在 `.Info(msg, args...)` 里 `zap.Field` 路径,把 20 个 canonical key 名的 `zap.Field` **剔除**(防止业务用 zap.Field 重复设 SDK 自管的 canonical 字段)。

下面列的是 `.With()` 14 个白名单(业务最常打交道的那一组):

### 5.1 SDK 自动绑定(不需要 middleware 管)

| 字段 | 来源 |
|---|---|
| `service` | `New("<name>")` 或 env `SERVICE_NAME` |
| `instance` | env `HOSTNAME` 或 `os.Hostname()` |
| `trace_id` | SDK 从 ctx-bound logger 的 ctx 抽 OTel SpanContext(ADR-16) |
| `span_id` | 同上 |

### 5.2 LoggerMiddleware 绑定(8 个,有值就绑)

`request_id` / `user_id` / `tenant_id` / `account_id` / `serial_number` / `model_no` / `firmware_version` / `device_msg_src`

### 5.3 特定场景手动绑

`session_id` / `task_name`(定时任务 / 业务流程级会话用 `logger.With(zap.String("task_name", "daily-report"))`)

**完整细节 + 变体命名 + IDTYPE 6 种值** → [fields-and-idtype.md](fields-and-idtype.md)
**Verify**:`Read <sdk>/spec/log_format.json`(字段列表 + null-omit / placeholder_input 契约)

---

## 6. IDTYPE 反射:3 条路径

SDK 自动把 PII 字段包成 `[IDTYPE:<type>:<value>]` token,前提是 agent 走对路径。

### 6.1 主路径:struct tag + `%v`(推荐,日常最多用)

```go
// types.go
type CreateUserReq struct {
    UserID int64  `json:"userId" sensitive:"user_id"`
    Email  string `json:"email"  sensitive:"email"`
    Name   string `json:"name"`                       // 非 PII,不打 tag
}

// logic
l.logger.Info("user_create user=%v", req)
// 输出 message: user_create user={"userId":"[IDTYPE:user_id:12345]","email":"[IDTYPE:email:...]","name":"alice"}
```

### 6.2 手动路径 A:`FormatIDType`(只有标量,没 struct)

```go
import "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go/sensitive"

uid := int64(12345)
l.logger.Info("login user_id=%v", sensitive.FormatIDType(sensitive.UserID, uid))
// 输出 message: login user_id=[IDTYPE:user_id:12345]
```

`IDType` 封闭枚举(**写错 = 编译失败**):

```go
sensitive.UserID       // [IDTYPE:user_id:...]
sensitive.Email        // [IDTYPE:email:...]
sensitive.DeviceSN     // [IDTYPE:device_sn:...]
sensitive.DeviceMAC    // [IDTYPE:device_mac:...]
sensitive.TicketID     // [IDTYPE:ticket_id:...]
sensitive.UserSN       // [IDTYPE:user_sn:...]
```

Verify:`Read <sdk>/go/sensitive/formatter.go`(找 `IDType` 常量定义块)

### 6.3 手动路径 B:`sensitive.Wrap`(走 zap.Field,不是 printf)

```go
// ❌ 错:zap.Any 直传 struct,SDK 反射不到 tag
logger.Info("user_created", zap.Any("user", user))   // 明文 PII

// ✅ 对:用 Wrap 让 zap 序列化时反射 tag
logger.Info("user_created", zap.Any("user", sensitive.Wrap(user)))
```

Verify:`Grep '^func Wrap\b' <sdk>/go/sensitive/`

**选型**:日常业务走 6.1 主路径。拿不到 struct 只有标量 → 6.2。硬要用 zap.Any → 6.3。详细对比见 [fields-and-idtype.md](fields-and-idtype.md)。

---

## 7. printf vs zap.Field 选型

SDK 的 `Info(msg, args...)` 自动检测 args 模式(verified [logger.go:275-335](https://gitlab.addx.ai/CLOUD/a4x-logger-sdk/-/blob/master/go/logger.go#L275)):

| args 内容 | SDK 走哪条路径 | 行为 |
|---|---|---|
| args 全是 `zap.Field` | **zap 结构化路径** | `filterReservedFields` 自动剔除 20 个 canonical key,剩余 field 作为 JSON 顶层字段输出 |
| args 混合(非全 zap.Field)| **printf 路径** | `fmt.Sprintf(msg, args...)` 渲染进 message;末尾 arg 是 `error` 且超占位符 → 抽到 `exception` 字段 |

### 两条路径都合法,按业务选

| 路径 | 写法 | 何时选 |
|---|---|---|
| **A. printf**(日常默认)| `l.logger.Info("event key=%v", v)` | 99% 业务场景 — grep / 日志阅读友好 |
| **B. 结构化 zap.Field** | `l.logger.Info("event", zap.Any("key", v))` | 字段需要 ES 精准查询 / 数值聚合等结构化分析 |

**关键差异:canonical key 在两路径的处理**

| canonical key(`user_id` 等 14 个)| 路径 A | 路径 B |
|---|---|---|
| 写在 message text 里(`user_id=%v`)| ✅ 期望 — **不算重复**,JSON 顶层独立字段由 middleware 注入,message 文本是给人眼 / grep 用 | n/a |
| 作为 zap.Field 传 | n/a | ❌ **必须删** — `zap.Int64("user_id", uid)` 会被 `filterReservedFields` 丢,留着既无效又 noise |

**禁止混用 printf 占位 + zap.Field**:`l.logger.Info("event count=%v", n, zap.String("source", src))` ❌ — SDK `allZapFields` 检查不通过,zap.Field 被当 printf arg,渲染出 `{Type:... Integer:...}` garbage。**要么全 printf,要么全 zap.Field**。

---

## 8. 禁用写法(含 why)

以下写法会让 SDK 保护失效或部分失效。**业务 Logic 一律走 `a4xlogger.FromContext(ctx).Info/Error(...)`**;`logx.Xxx` 留给框架(go-zero)自身的日志桥接,业务不主动写。

| 写法 | 为什么禁 |
|---|---|
| `log.Info(fmt.Sprintf("user %v", u))` | `fmt.Sprintf` 在 SDK 之前执行,SDK 拿到的已经是字符串 → **IDTYPE 反射失效** → PII 明文落盘 |
| `log.Info("user " + u.String())` | 同上 —— 字符串拼接 SDK 也反射不到原 struct |
| `logx.Infow("msg", logx.Field("user", u))` | 走 go-zero logx 管线,经 `a4xgozero.NewWriter` adapter 后:**struct sensitive tag 的 IDTYPE 反射仍生效**(`gozero/adapter.go:198-257 convertValue`);**但 canonical 字段(trace_id / user_id 等)不注入**(`logx.Infow` 是全局调用,无 ctx)。结果:业务侧字段名手填、Tier 1/2 自动注入缺失 → 输出日志和 SDK pipeline 不等价。业务 Logic 应该 `FromContext(ctx).Infow(...)` 而非 `logx.Infow(...)` |
| `logx.Infof / Warnf / Errorf / Debugf("user %v", u)` | logx.Xxxf 内部 `fmt.Sprintf` 在前,adapter 收到时已是字符串 —— printf 路径下 IDTYPE 反射**失效**(struct 已被 stringify)。即使 `logx.SetWriter(a4xgozero.NewWriter)` 之后也救不回来 |
| `log.Fatalf("msg %v", err)`(stdlib)| `os.Exit` 跳过所有 defer(含 `logger.Sync()` 和资源清理)→ 日志可能丢最后一行 + 资源泄漏。**SDK 层面禁用 Fatal**,迁移时拆成 `logger.Errorf(...); os.Exit(1)` |

> **logx.Xxx 边界澄清**:adapter 路由路径 + struct 形式参数 → IDTYPE 仍生效;adapter 路由路径 + printf-style(`Infof/%v`)→ IDTYPE 失效。业务 Logic 不应依赖 adapter 兜底,**`FromContext(ctx).Info` 才是 canonical 字段 + IDTYPE 双闭环的正解**。adapter 主要负责 go-zero 框架自身的日志桥接(如 access log),业务直调时缺 ctx 拿不到 trace_id。

详细禁止表见 [common-pitfalls.md](common-pitfalls.md) + [SDK usage.md 附录 A.3](https://gitlab.addx.ai/CLOUD/a4x-logger-sdk/-/blob/master/docs/architecture/go/usage.md#appendix-a)。

---

## 9. SDK 行为承诺(agent 引用时标清楚)

| 行为 | 描述 | Verify |
|---|---|---|
| **error 自动抽 exception** | `.Error(msg, args..., err)` — 若末尾 arg 是 error 且 `len(args) > countPlaceholders(msg)`,err 抽到 `exception` 字段,不进 message | Read `<sdk>/go/logger.go:275-296` |
| **OTel ctx 自动抽 trace_id** | `.WithContext(ctx)` 挂的 ctx 在 emit 时自动抽 SpanContext,补 `trace_id` / `span_id` | Read `<sdk>/go/logger.go`(找 resolve 前的 ctx 抽取)|
| **filterReservedFields 防 canonical 覆盖** | 全 zap.Field 路径里 20 个 canonical key 名被自动丢弃 | Read `<sdk>/go/logger.go:300-319` |
| **null-omit 规则** | `.With()` 没绑的字段 JSON 输出里 key 直接省略,不是空值 | `<sdk>/spec/log_format.json` `emission_policy.unified_rule` |
| **placeholder_input_invariant** | SDK 必须看到原始对象(不是 pre-formatted 字符串)才能反射 | `<sdk>/spec/log_format.json` `emission_policy.placeholder_input_invariant` |
| **SDK 永不 panic 到业务** | 内部异常 recover → 输出 `internal_error` 字段的日志 + 业务 goroutine 继续 | Read `<sdk>/go/logger.go` `safeLog`(ADR-15)|
| **`Fatal` 不存在** | SDK Logger 没有 Fatal/Fatalf 方法 — 业务自己 `logger.Error(...); os.Exit(1)` | `Grep '^func.*Logger\) Fatal' <sdk>/go/` 应无输出 |

---

## 10. Verify SDK 入口表(agent 引用前查这张)

**包名 → 目录映射**(**先用这张表定位对文件夹**,再跑下面的 Verify 命令。`a4xlogger.*` 不在 `sensitive/` 子目录,方向别搞反):

| 包名 | 目录 | 主要 API |
|---|---|---|
| `a4xlogger` | `<sdk>/go/*.go` | `New` / `FromContext` / `WithLogger` / `Nop` / `NewFromZap` / `NewRequestID` + `*Logger` 方法 |
| `sensitive` | `<sdk>/go/sensitive/*.go` | `FormatIDType` / `Wrap` / `IDType` 常量 / `MaskConfig` / `NewEmptyConfig` |
| `testutil` | `<sdk>/go/sensitive/testutil/*.go` | `NewLogCapture` / `LogCapture` |
| `a4xgozero` | `<sdk>/go/gozero/*.go` | `NewWriter` |
| spec | `<sdk>/spec/*.json` | canonical 字段 / 跨语言契约 |

**Verify 命令**(定位好目录后用):

| 声称类型 | 命令 |
|---|---|
| `a4xlogger` 公共函数(`New` / `FromContext` / `NewRequestID` 等)| `Grep '^func <Name>\b' --type=go <sdk>/go/*.go` |
| `*Logger` 方法(`.With` / `.WithContext` / `.Info` 等)| `Grep 'func \(.*\*Logger\) <Name>\b' --type=go <sdk>/go/*.go` |
| `sensitive` API(`FormatIDType` / `Wrap` 等)| `Grep '^func <Name>\b' --type=go <sdk>/go/sensitive/` |
| `sensitive.IDType` 常量(`UserID` / `Email` 等)| `Grep '<Name>\s+IDType\s*=' --type=go <sdk>/go/sensitive/formatter.go` |
| `testutil` API | `Grep '^func <Name>' --type=go <sdk>/go/sensitive/testutil/` |
| `a4xgozero` API | `Grep '^func <Name>' --type=go <sdk>/go/gozero/` |
| Canonical 字段 / spec 契约 | `Read <sdk>/spec/log_format.json` |
| logger.yaml schema | `Read <sdk>/go/sensitive/mask.go`(`MaskConfig` struct 的 `yaml:` tag)|
| 版本行为变化 | `git -C <sdk> log <tagA>..<tagB> -- <path>` |
| 具体 SDK 行为 | Read 源码,引用 `file:line` |

**捷径**:首次接入任务 Read `<sdk>/docs/architecture/go/usage.md` + `<sdk>/spec/log_format.json` 各一次,80% 引用都覆盖到。

---

## 11. 项目层 convention vs SDK 硬规则(不要混淆)

| 是 SDK 硬规则(agent 必须遵守)| 是项目层 convention(新写日志推荐,migration 不强制)|
|---|---|
| `logx.Xxx` / `fmt.Sprintf` / 字符串拼接打日志 ❌ 禁 | `action=<name> result=<state>` message 格式 |
| `log.Fatal*` 拆成 `Error + os.Exit(1)` | canonical key 命名统一(`user_id` 不是 `uid`)|
| err 当最后参数触发 exception 抽取 | Info/Warn/Error level 选型 |
| IDTYPE 反射必须走 3 条路径之一 | message 里是否加 `duration_ms` |
| canonical key 通过 middleware `.With()` 绑,不在 per-call 重复传 | action 命名 snake_case verb_noun |

**项目层 convention 看** [log-style-guide.md](log-style-guide.md)。**migration 时不强行 retrofit**,见 [execution.md](execution.md) Step 5 的"机械替换 4 规则"。
