# Log Style Guide:从 0 开始怎么写日志

> ⚠️ **本规约是"新写日志"的推荐 pattern,不是 SDK 强制**。
> **migration(已有日志迁移)不强行 retrofit** — 那条路径见 [execution.md §3 机械替换 4 规则](go/execution.md)。
> 本文件的 `action=/result=` / canonical key 命名 / 测试断言 pattern 等属**项目层软约定**,
> 目的是跨服务对齐,**不是 agent 在迁移时要把 162 条 `log.Printf` 全部重写的理由**。

**适用场景**:
- 全新服务从 day 1 接入 a4x-logger-sdk(greenfield,没历史 log 调用可迁)
- 迁移项目里遇到"要新增日志点,不是改现有的"场景
- 想对齐团队日志规约的场景

本 guide 只讲**怎么写**。**怎么接入(main/middleware/路由等)**按语言看:
- Go → [go/sdk-usage.md](go/sdk-usage.md) + [go/execution.md](go/execution.md)
- Java → [java/sdk-usage.md](java/sdk-usage.md) + [java/execution.md](java/execution.md)
- Python → [python/sdk-usage.md](python/sdk-usage.md) + [python/execution.md](python/execution.md)

SDK 端的权威说明在 [a4x-logger-sdk/docs/architecture/go/usage.md §1](https://gitlab.addx.ai/CLOUD/a4x-logger-sdk/-/blob/master/docs/architecture/go/usage.md),本 guide 补充**业务侧约定**(SDK 不管,但跨服务对齐)。

---

## 1. 业务日志的语义 pattern

每条业务日志的 message 都应该能回答:**做了什么(action)+ 结果(result)+ 关键业务字段**。

### 推荐 pattern

Go:
```go
log.Info("<action_name> result=<result> <key>=<val> <key>=<val> ...")
```

Java:
```java
log.info("<action_name> result=<result> <key>={} <key>={}", val1, val2);
```

Python:
```python
logger.info("<action_name> result=<result> <key>=%s <key>=%s", val1, val2)
```

### 固定字段

| 字段 | 必填 | 取值 |
|---|---|---|
| `action=` | ✅ 必填 | 动词_名词,snake_case,见下表 |
| `result=` | ✅ 必填 | `success` / `empty` / `not_found` / `denied` / `failed` / 其他明确状态 |
| `duration_ms=` | 建议 | 本次业务操作耗时(整数) |
| 其他业务字段 | 按需 | user_id / order_id / count / source 等 |

### `action=` 命名约定

| 类型 | 范式 | 示例 |
|---|---|---|
| CRUD | `<entity>_<verb>` | `order_create` / `user_update` / `device_delete` |
| 列表/查询 | `<entity>_list` / `<entity>_search` | `timeline_list_events` / `postcard_search` |
| 状态变更 | `<entity>_<new_state>` | `order_shipped` / `user_activated` |
| 外部调用 | `<target>_called` / `<target>_responded` | `iot_service_called` / `oauth_callback_received` |
| 系统事件 | `<event>` | `migration_started` / `cache_warmed` |

### 例子(符合规约)

Go:
```go
// 读路径
l.logger.Info("timeline_list_events result=empty user_id=%v event_count=0 total=0 duration_ms=%d",
    uid, elapsed.Milliseconds())

// 写路径
l.logger.Info("postcard_create result=success postcard_id=%v user_id=%v",
    pc.ID, uid)

// 外部调用
l.logger.Info("iot_service_called result=success request_id=%v http_status=200 duration_ms=%d",
    reqID, elapsed.Milliseconds())

// 异常
l.logger.Error("order_create failed user_id=%v", uid, err)
```

Java:
```java
// 读路径
log.info("timeline_list_events result=empty user_id={} event_count=0 total=0 duration_ms={}",
    uid, elapsed.toMillis());

// 写路径
log.info("postcard_create result=success postcard_id={} user_id={}", pc.getId(), uid);

// 外部调用
log.info("iot_service_called result=success request_id={} http_status=200 duration_ms={}",
    reqId, elapsed.toMillis());

// 异常(throwable 放最后)
log.error("order_create failed user_id={}", uid, e);
```

Python:
```python
# 读路径
logger.info("timeline_list_events result=empty user_id=%s event_count=0 total=0 duration_ms=%s",
    uid, int(elapsed_ms))

# 写路径
logger.info("postcard_create result=success postcard_id=%s user_id=%s", pc.id, uid)

# 外部调用
logger.info("iot_service_called result=success request_id=%s http_status=200 duration_ms=%s",
    req_id, int(elapsed_ms))

# 异常(在 except 块里用 exception)
logger.exception("order_create failed user_id=%s", uid)
```

### 反面例子

Go:
```go
// ❌ 没 action,读 log 不知道发生了什么
log.Info("processing done")

// ❌ 没 result,成功失败都一个样
log.Info("action=order_create user_id=%v", uid)

// ❌ action 是名词堆砌
log.Info("action=order operation done")

// ❌ 混用中英文导致查询困难
log.Info("action=创建订单 result=成功")
```

Java:
```java
// ❌ 没 action
log.info("处理完成");

// ❌ 没 result
log.info("order_create user_id={}", uid);

// ❌ 字符串拼接(IDTYPE 反射失效 + 查询难)
log.info("order created for user " + uid.toString());
```

Python:
```python
# ❌ 没 action
logger.info("处理完成")

# ❌ 没 result
logger.info("order_create user_id=%s", uid)

# ❌ f-string(IDTYPE 反射失效,PII 明文落盘)
logger.info(f"order created for user {uid}")
```

---

## 2. 拿到 logger 的两种姿势

### 2.1 Struct 嵌入(**推荐**,go-zero Logic 标准模式 / Java Service 标准模式)

Go:
```go
type ListOrderLogic struct {
    ctx    context.Context
    svcCtx *svc.ServiceContext
    logger *a4xlogger.Logger    // ← 加这行
}

func NewListOrderLogic(ctx context.Context, svcCtx *svc.ServiceContext) *ListOrderLogic {
    return &ListOrderLogic{
        ctx:    ctx,
        svcCtx: svcCtx,
        logger: a4xlogger.FromContext(ctx),   // ← 构造时取一次
    }
}

func (l *ListOrderLogic) ListOrder(req *types.ListOrderReq) (*types.ListOrderResp, error) {
    l.logger.Info("order_list result=success user_id=%v", req.UserID)
    // ... 后续方法都用 l.logger.Xxx(...)
}
```

Java:
```java
@Service
public class OrderService {
    // Spring 标准:每个 class 一个 static logger,SLF4J 直接用
    private static final Logger log = LoggerFactory.getLogger(OrderService.class);

    public ListOrderResponse listOrder(ListOrderRequest req) {
        log.info("order_list result=success user_id={}", req.getUserId());
        // ... MDC 里已有 canonical 字段,log.info 自动带上
    }
}
```

Python:
```python
import structlog

class OrderService:
    def __init__(self):
        # structlog: 模块级 logger,不需要 class 持有引用
        self._log = structlog.get_logger()

    def list_order(self, req):
        self._log.info("order_list result=success user_id=%s", req.user_id)
        # contextvars 里已有 canonical 字段,log.info 自动带上
```

**什么时候用**:任何 class/struct 里要多处打日志的场景(Logic / Service / Worker)。

### 2.2 函数式(函数内直接取 logger)

Go:
```go
// Gin / net/http handler / 工具函数
func doSomething(ctx context.Context, user User) {
    log := a4xlogger.FromContext(ctx)
    log.Info("user_processed result=success user_id=%v", user.ID)
}
```

Java:
```java
// 工具函数 / static 方法里同样 LoggerFactory
private static final Logger log = LoggerFactory.getLogger(MyUtil.class);

public static void doSomething(User user) {
    log.info("user_processed result=success user_id={}", user.getId());
}
```

Python:
```python
# 函数顶层 / 工具函数
import structlog

logger = structlog.get_logger()

def do_something(user):
    logger.info("user_processed result=success user_id=%s", user.user_id)
```

**什么时候用**:没 struct 分层、或只打 1-2 条的场景(handler 内联、utility 函数、一次性脚本)。

### ⚠️ Go Middleware 里**不用** `FromContext`

前置 middleware(UserId / Tenant / Device 鉴权等)跑在 `LoggerMiddleware` 之前,**ctx 里还没有绑 logger**,`FromContext` 返回 `Nop()`,日志**静默丢失**。

详见 [go/common-pitfalls.md](go/common-pitfalls.md)。Go middleware 要 struct 持 base logger,用 `m.logger.WithContext(r.Context())`。

Java 对应陷阱:Filter 没在 `finally` 里 `MDC.clear()`,线程复用导致字段泄漏——详见 [java/common-pitfalls.md](java/common-pitfalls.md)。

---

## 3. Level 选型

| Level | 什么时候用 | 例子 |
|---|---|---|
| `Debug` | 排查用,生产默认不输出 | 详细的 JWT claim / SQL 查询参数 |
| `Info` | 正常业务事件 | `order_create result=success` / `timeline_list result=empty` |
| `Warn` | 非正常但不影响功能 | JWT 过期、Redis 连接失败走 fallback、重试命中 |
| `Error` | 业务失败、需要排查 | DB 写入失败、外部服务 500、解析失败 |
| ~~Fatal~~ | **禁用** | 会直接 `os.Exit` / `System.exit()` / `sys.exit()`,杀进程,容器都不给机会 recover |

**区分 Warn vs Error 的原则**:能自动恢复 / 走 fallback / 用户下一次还能正常用 → Warn。影响本次业务结果 / 需要人介入 → Error。

**三语 Level API**:

Go:
```go
l.logger.Debug("jwt_claim_parsed sub=%v exp=%v", sub, exp)
l.logger.Info("order_create result=success order_id=%v", oid)
l.logger.Warn("redis_connect_failed result=fallback attempt=%d", attempt)
l.logger.Error("db_write_failed order_id=%v", oid, err)
// Fatal — 禁止使用
```

Java:
```java
log.debug("jwt_claim_parsed sub={} exp={}", sub, exp);
log.info("order_create result=success order_id={}", oid);
log.warn("redis_connect_failed result=fallback attempt={}", attempt);
log.error("db_write_failed order_id={}", oid, e);
// log.error("...", e) — throwable 最后,SDK 自动抽到 exception 字段
// System.exit() — 禁止使用
```

Python:
```python
logger.debug("jwt_claim_parsed sub=%s exp=%s", sub, exp)
logger.info("order_create result=success order_id=%s", oid)
logger.warning("redis_connect_failed result=fallback attempt=%s", attempt)
logger.exception("db_write_failed order_id=%s", oid)   # exception 自动捕 traceback
# sys.exit() — 禁止使用
```

---

## 4. 错误日志正确写法

### 推荐:error 作最后一个参数,SDK 自动抽

Go:
```go
// SDK 看到最后一个参数是 error,会自动提取到 "exception" 字段
l.logger.Error("db_query_failed user_id=%v", uid, err)

// 输出:
// {"level":"ERROR","message":"db_query_failed user_id=12345","exception":"mysql: connection refused",...}
```

Java:
```java
// SLF4J 约定:throwable 作最后一个参数,SDK Format 层自动抽到 exception 字段
log.error("db_query_failed user_id={}", uid, e);
// → {"level":"ERROR","message":"db_query_failed user_id=12345","exception":"...stacktrace..."}
```

Python:
```python
# except 块里用 logger.exception — 自动捕获当前 traceback 到 exception 字段
try:
    result = db.query(sql, uid)
except Exception:
    logger.exception("db_query_failed user_id=%s", uid)
# → {"level":"ERROR","message":"db_query_failed user_id=12345","exception":"Traceback ..."}
```

### 多个 error 传参

Go:
```go
// err1, err2 都作为参数,SDK 提取最后一个 error 到 exception
l.logger.Error("cleanup_partial_failed order_id=%v", oid, err1, err2)
```

Java:
```java
// SLF4J 只支持最后一个 throwable,前面的 exception 信息手动放进 message
log.error("cleanup_partial_failed order_id={} first_err={}", oid, err1.getMessage(), err2);
```

Python:
```python
# 多个 exception:先记第一个,再 chain
try:
    cleanup()
except Exception as e1:
    logger.exception("cleanup_partial_failed order_id=%s first_err=%s", oid, str(e1))
```

### ❌ 不要自己手动拼 error 到 message

Go:
```go
// ❌ 错 —— 错误信息混进 message,下游查询时 parse 麻烦,exception 字段空
l.logger.Error("db_query_failed: %v", err)

// ❌ 错 —— error 字段名自定义,下游搜 exception 搜不到
l.logger.Info("failed with err=%v", err)
```

Java:
```java
// ❌ 错 —— 字符串拼接,exception 字段空
log.error("db_query_failed: " + e.getMessage());

// ❌ 错 —— 手动 format,e 的 stacktrace 丢失
log.error("db_query_failed: {}", e.getMessage());
// ✅ 正确:
log.error("db_query_failed", e);
```

Python:
```python
# ❌ 错 —— f-string 拼入 message,exception 字段空
logger.error(f"db_query_failed: {e}")

# ❌ 错 —— 没用 exception,traceback 丢失
logger.error("db_query_failed: %s", str(e))
# ✅ 正确:在 except 块里
logger.exception("db_query_failed")
```

---

## 5. PII 字段:3 种路径选一

**主路径**(日常推荐):struct + 语言 tag/注解/metadata + 占位符。

Go:
```go
// types.go
type CreateOrderReq struct {
    UserID int64  `json:"userId" sensitive:"user_id"`
    Email  string `json:"email" sensitive:"email"`
}

// logic
l.logger.Info("order_create result=success req=%v", req)
// message 自动渲染成:
// order_create result=success req={"userId":"[IDTYPE:user_id:12345]","email":"[IDTYPE:email:***]"}
```

Java:
```java
// POJO
public class CreateOrderReq {
    @SensitiveField(IDType.USER_ID)
    private Long userId;

    @SensitiveField(IDType.EMAIL)
    private String email;
}

// Service
log.info("order_create result=success req={}", req);
// message 自动渲染成:
// order_create result=success req={"userId":"[IDTYPE:user_id:12345]","email":"[IDTYPE:email:***]"}
```

Python:
```python
from dataclasses import dataclass, field

@dataclass
class CreateOrderReq:
    user_id: int = field(metadata={"sensitive": "user_id"})
    email: str = field(metadata={"sensitive": "email"})

# handler / service
logger.info("order_create result=success req=%s", req)
# message 自动渲染成:
# order_create result=success req={"user_id": "[IDTYPE:user_id:12345]", "email": "[IDTYPE:email:***]"}
```

**手动路径 A**(拿不到 struct 只有标量):

Go:
```go
userID := int64(12345)
l.logger.Info("login result=success user_id=%v",
    sensitive.FormatIDType(sensitive.UserID, userID))
```

Java:
```java
Long userId = 12345L;
log.info("login result=success user_id={}",
    SensitiveFormatter.format(IDType.USER_ID, userId));
```

Python:
```python
from a4x_logger.sensitive import format_idtype

user_id = 12345
logger.info("login result=success user_id=%s",
    format_idtype("user_id", user_id))
```

**手动路径 B**(必须走 zap 结构化字段,Go 特有):

Go:
```go
l.logger.Info("user_created",
    zap.Any("user", sensitive.Wrap(user)))
```

详见 [fields-and-idtype.md](fields-and-idtype.md)。

---

## 6. 禁止写法清单

Go:
```go
// ❌ 1. fmt.Sprintf — IDTYPE 反射失效,PII 明文落盘
l.logger.Info(fmt.Sprintf("user %v", user))

// ❌ 2. 字符串拼接 — 同上
l.logger.Info("user " + user.String())

// ⚠️ 3. 业务 Logic 直接调 logx.Infow — adapter 能做 IDTYPE 但无 ctx,canonical 字段不注入
//        应该用 a4xlogger.FromContext(ctx).Info 拿 ctx-bound logger
logx.Infow("msg", logx.Field("user", u))

// ❌ 4. logx.Infof — printf 路径,先 Sprintf,IDTYPE 反射失效
logx.Infof("user %v", user)

// ❌ 5. 中文 / 表情 / 无 action / 无 result 的口水日志
l.logger.Info("哦,出错啦 😅 请联系管理员")
```

Java:
```java
// ❌ 1. 字符串拼接 — IDTYPE 反射失效,PII 明文落盘
log.info("user " + user.toString());

// ❌ 2. String.format — 同上
log.info(String.format("user %s", user));

// ❌ 3. System.out.println — 绕过 SDK 管线
System.out.println("user: " + user);

// ❌ 4. 用 {} 但提前序列化了 PII
String userStr = user.toString();  // toString() 把 PII 字段明文输出
log.info("user {}", userStr);

// ❌ 5. 中文 / 无 action / 无 result
log.info("哦,出错了");
```

Python:
```python
# ❌ 1. f-string — IDTYPE 反射失效,PII 明文落盘
logger.info(f"user {user}")

# ❌ 2. .format() — 同上
logger.info("user {}".format(user))

# ❌ 3. 手动 % 先拼 — 同上
logger.info("user %s" % user)

# ❌ 4. str 拼接 — 同上
logger.info("user " + str(user))

# ❌ 5. print() — 绕过 SDK 管线
print(f"user: {user}")

# ❌ 6. 中文 / 无 action / 无 result
logger.info("哦,出错了")
```

完整禁止表见各语言 [go/common-pitfalls.md](go/common-pitfalls.md) / [java/common-pitfalls.md](java/common-pitfalls.md) / [python/common-pitfalls.md](python/common-pitfalls.md)。

---

## 7. 从 0 写日志断言测试

### 7.1 测试文件模板

Go:
```go
package <domain>_test

import (
    "context"
    "strings"
    "testing"

    a4xlogger "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go"
    "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go/sensitive"
    "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go/sensitive/testutil"
)

func TestListOrder_LogsSuccess(t *testing.T) {
    // 1. 构造捕获式 logger
    cap := testutil.NewLogCapture(t, sensitive.NewEmptyConfig())

    // 2. 把 logger 绑到 ctx
    ctx := a4xlogger.WithLogger(context.Background(),
        a4xlogger.NewFromZap(cap.Logger))

    // 3. 跑业务
    l := <domain>.NewListOrderLogic(ctx, testSvcCtx)
    _, err := l.ListOrder(&types.ListOrderReq{UserID: 42, Page: 1})
    if err != nil {
        t.Fatalf("unexpected: %v", err)
    }

    // 4. 断言日志
    last := cap.Last(t)
    msg, _ := last["message"].(string)

    if !strings.Contains(msg, "order_list") {
        t.Fatalf("missing action, got:\n%s", msg)
    }
    if !strings.Contains(msg, "result=success") {
        t.Fatalf("missing result=success, got:\n%s", msg)
    }
    if uid, _ := last["user_id"].(float64); int64(uid) != 42 {
        t.Fatalf("expected user_id=42, got %v", last["user_id"])
    }
}
```

Java:
```java
@ExtendWith(LogCaptureExtension.class)
class OrderServiceTest {

    @Inject
    LogCapture logCapture;   // SDK test-jar 提供

    @Test
    void listOrder_logsSuccess() {
        OrderService svc = new OrderService();
        svc.listOrder(new ListOrderRequest(42L, 1));

        LogCapture.Entry last = logCapture.last();
        assertThat(last.getMessage()).contains("order_list");
        assertThat(last.getMessage()).contains("result=success");
        assertThat(last.getLevel()).isEqualTo("INFO");
        // canonical 字段断言
        assertThat(logCapture.getMdcValue("user_id")).isEqualTo("42");
    }
}
```

Python:
```python
import pytest
from a4x_logger.testing import log_capture, assert_log_emit

def test_list_order_logs_success(log_capture):
    svc = OrderService()
    svc.list_order(ListOrderRequest(user_id=42, page=1))

    last = log_capture.last()
    assert "order_list" in last["message"]
    assert "result=success" in last["message"]
    assert last["level"] == "info"

    # 断言 canonical 字段
    assert_log_emit(log_capture, action_contains="order_list", result="success")
```

### 7.2 常见 assertion 速查

| 想断言的 | Go | Java | Python |
|---|---|---|---|
| message 含某字段 | `strings.Contains(msg, "user_id=")` | `assertThat(msg).contains("user_id=")` | `"user_id=" in last["message"]` |
| IDTYPE 渲染正确 | `strings.Contains(msg, "[IDTYPE:user_id:")` | `assertThat(msg).contains("[IDTYPE:user_id:")` | `"[IDTYPE:user_id:" in last["message"]` |
| canonical 字段值 | `last["user_id"]` | `logCapture.getMdcValue("user_id")` | `last["user_id"]` |
| 级别是 ERROR | `last["level"] == "ERROR"` | `last.getLevel() == "ERROR"` | `last["level"] == "error"` |
| error 被自动抽取 | `last["exception"] != nil` | `last.getException() != null` | `"exception" in last` |
| 多条日志 | `cap.Lines(t)` | `logCapture.all()` | `log_capture.all()` |

### 7.3 命名规则(推荐)

Go:
```go
// ✅ camelCase,不用 _ (Sonar go:S100 风险,虽现已放宽)
func TestListOrderLogsSuccess(t *testing.T) {}
func TestListOrderLogsEmptyResult(t *testing.T) {}
func TestListOrderLogsErrorOnDBFailure(t *testing.T) {}
```

Java:
```java
// ✅ 方法名用 camelCase + 场景描述
@Test void listOrder_logsSuccess() {}
@Test void listOrder_logsEmptyResult() {}
@Test void listOrder_logsErrorOnDbFailure() {}
```

Python:
```python
# ✅ snake_case,pytest 标准
def test_list_order_logs_success(): ...
def test_list_order_logs_empty_result(): ...
def test_list_order_logs_error_on_db_failure(): ...
```

---

## 8. Greenfield 项目的 rollout 建议

新服务第一次上线,**别急着一次性在所有 logic 里打满日志**。按节奏:

1. **Day 1**:每个 Logic / Service 至少 1 条 `action=<name> result=success/failed` 入口日志(成功路径一条,失败路径一条)
2. **Day 2-N**:遇到排查难受的场景,加针对性 log(中间状态、关键决策分支)
3. **季度性 audit**:log 量 vs 信息密度,没人看的 log 删掉

**避免的反模式**(三语通用):
- 在循环里打 log(一次请求几千条)
- 在 Debug 级里塞 PII(即使 Debug 默认不输出,偶尔开 Debug 就全量泄漏)
- 每层都打"进入函数 / 退出函数"(做 profiling 用 OTel span,不是 log)
- Go: `fmt.Sprintf` 预格式化 / Java: `String.format` / Python: f-string + `%s %` 在 logger 参数外拼接
