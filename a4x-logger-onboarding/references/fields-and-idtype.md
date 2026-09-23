# Canonical 字段 + IDTYPE Tag 契约

> **SSOT 在 SDK 仓库**:
> - `spec/log_format.json` — canonical 字段权威定义(字段分类 / null-omit 规则 / emission policy)
> - `spec/sensitive_types.json` — 6 种 IDTYPE 枚举权威列表
>
> 本文档是 skill 用的速查,每个关键字段 / IDTYPE 变体均给出 **Go / Java / Python 三语 inline codeblock**。冲突时以 `spec/` 为准。

---

## Part 1:Canonical 字段(来自 spec/log_format.json)

`spec/log_format.json` 中共定义 **20 个 canonical 字段**,分 4 类:Fixed(7)/ Tracing(3)/ Context(9)/ Error(1)。SDK 只允许通过三语各自的绑定 API 写入这 20 个字段,**其他字段名被静默忽略**——防止业务乱加字段造成下游查询混乱。

### 1.1 Fixed 字段(7 个,SDK 自动填充)

这 7 个字段由 SDK 在 emit 时自动填充,业务代码不需要也不应该手动绑。

| 字段 | 来源 / 取值 |
|---|---|
| `timestamp` | emit 时刻 ISO-8601 |
| `level` | 日志级别(`DEBUG` / `INFO` / `WARN` / `ERROR`) |
| `service` | 初始化参数 / `SERVICE_NAME` env |
| `instance` | `HOSTNAME` env 或 `os.Hostname()` |
| `logger` | logger 名称(Go: 包路径;Java: 类名;Python: 模块名) |
| `message` | format 后的最终消息字符串 |
| `thread` | 线程 / goroutine 名(Go: goroutine id;Java: thread name;Python: thread name) |

**SDK 初始化写法(service + instance 来源)**:

Go:
```go
logger := a4xlogger.New("<service-name>",
    a4xlogger.WithConfigFile("etc/logger.yaml"),
)
// service = "<service-name>", instance = os.Hostname()
```

Java:
```java
// application.yml — SDK 自动读取
a4x-logger:
  service-name: my-service    # 默认读 SERVICE_NAME env
  instance: ${HOSTNAME}       # 不要写 ${HOSTNAME:unknown} — :- 会让 null-omit 失效
```

Python:
```python
from a4x_logger import setup_logging

setup_logging("user-service", config_file="logger.yaml")
# service = "user-service", instance = socket.gethostname()
```

---

### 1.2 Tracing 字段(3 个)

| 字段 | 含义 | 来源 |
|---|---|---|
| `trace_id` | OTel W3C 32-hex | OTel SpanContext(SDK emit 时自动抽) |
| `span_id` | OTel W3C 16-hex | 同上 |
| `request_id` | 单请求唯一 ID | 业务 middleware 生成或透传 X-Request-Id header |

**null-omit**:没部署 OTel → `trace_id` / `span_id` key 直接不出现,不报错。SDK 推荐版本 Go v0.2.2 / Java 1.0.0 / Python 1.0.0 起自动处理。

**`request_id` 绑定写法**:

Go:
```go
// LoggerMiddleware 里
reqID := r.Header.Get("X-Request-Id")
if reqID == "" {
    reqID = a4xlogger.NewRequestID()
}
log = log.With(zap.String("request_id", reqID))
```

Java:
```java
// Filter 里 MDC 写入
String reqId = Optional.ofNullable(request.getHeader("X-Request-Id"))
    .orElseGet(RequestId::newRequestId);
MDC.put("request_id", reqId);
```

Python:
```python
# ASGI/WSGI middleware 里
from a4x_logger import new_request_id
from structlog.contextvars import bind_contextvars

req_id = request.headers.get("X-Request-Id") or new_request_id()
bind_contextvars(request_id=req_id)
```

---

### 1.3 Context 字段(9 个,有值才绑,null-omit)

| 字段 | 含义 | 适用场景 | 建议 |
|---|---|---|---|
| `user_id` | 认证用户 ID | 所有面向用户的服务 | **必装** |
| `tenant_id` | 多租户 ID | 多租户 SaaS | 多租户项目必装 |
| `serial_number` | 设备序列号 | IoT / 设备管理 | IoT 必装 |
| `model_no` | 设备型号 | IoT | IoT 必装 |
| `firmware_version` | 固件版本 | IoT | IoT 必装 |
| `device_msg_src` | 消息来源 | 所有服务 | **建议装**,服务端写死 `"cloud"` |
| `account_id` | 账户 ID | 账户分层业务 | 按需 |
| `session_id` | 流程级会话 ID | 跨请求业务流程 | 按需 |
| `task_name` | 定时任务名 | 后台定时任务 | 任务入口手动绑 |

**null-omit 规则**:没绑的字段,JSON 里 key 直接不出现(不是空值、不是 null):
- 未登录访客访问 /health → `user_id` 字段缺失 ✅
- **不会**出现 `"user_id":0` 或 `"user_id":""` 这种有歧义的数据

**`user_id` 绑定写法(最常用 Context 字段)**:

Go:
```go
// LoggerMiddleware 里,从 ctx 读鉴权 middleware 放入的 userID
if uid, ok := ctx.Value(auth.UserIDKey).(int64); ok {
    log = log.With(zap.Int64("user_id", uid))
}
```

Java:
```java
// Filter 里 MDC 写入(canonical key 必须是 "user_id")
String userId = extractUserIdFromJwt(request);
if (userId != null) {
    MDC.put("user_id", userId);    // ✅ canonical name
}
// MDC.put("uid", userId);         // ❌ alias 需在 application.yml 配置
```

Python:
```python
# ASGI/WSGI middleware 里
from structlog.contextvars import bind_contextvars

user_id = extract_user_id_from_jwt(request)
if user_id:
    bind_contextvars(user_id=str(user_id))
```

**`task_name` 绑定写法(手动绑,非 per-request)**:

Go:
```go
func runDaily(ctx context.Context, baseLogger *a4xlogger.Logger) {
    log := baseLogger.WithContext(ctx).With(
        zap.String("task_name", "daily-report"),
        zap.String("request_id", a4xlogger.NewRequestID()),
    )
    log.Info("report_generation result=started")
}
```

Java:
```java
@Scheduled(cron = "0 0 2 * * ?")
public void runDailyReport() {
    MDC.put("task_name", "daily-report");
    MDC.put("request_id", RequestId.newRequestId());
    try {
        log.info("report_generation result=started");
        // ... 任务逻辑
    } finally {
        MDC.clear();    // ← 必须放 finally
    }
}
```

Python:
```python
from structlog.contextvars import bind_contextvars, clear_contextvars
from a4x_logger import new_request_id
import structlog

logger = structlog.get_logger()

def run_daily_report():
    bind_contextvars(task_name="daily-report", request_id=new_request_id())
    try:
        logger.info("report_generation result=started")
        # ... 任务逻辑
    finally:
        clear_contextvars()
```

---

### 1.4 Error 字段(1 个)

| 字段 | 含义 | 来源 |
|---|---|---|
| `exception` | 错误 / 异常信息 | SDK 自动从最后一个 error/exception 参数抽取 |

SDK 约定:把 error 放为最后一个参数,SDK 自动抽取到 `exception` 字段。

Go:
```go
// ✅ error 作最后一个参数,SDK 自动抽取到 exception 字段
l.logger.Error("db_query_failed user_id=%v", uid, err)
// → {"level":"ERROR","message":"db_query_failed user_id=12345","exception":"mysql: conn refused",...}
```

Java:
```java
// ✅ throwable 作最后一个参数,SDK 自动抽取到 exception 字段
log.error("db_query_failed user_id={}", uid, e);
// → {"level":"ERROR","message":"db_query_failed user_id=12345","exception":"...stacktrace..."}
```

Python:
```python
# ✅ 在 except 块里用 logger.exception — 自动捕获当前 traceback 到 exception 字段
try:
    result = db.query(sql)
except Exception:
    logger.exception("db_query_failed user_id=%s", uid)
# → {"level":"ERROR","message":"db_query_failed user_id=12345","exception":"Traceback ..."}
```

---

### 1.5 字段可见性时序

| 字段 | 可见起点 | 之前的日志带不带? |
|---|---|---|
| `trace_id` / `span_id` | OTel SpanContext 注入后 | 任何用 ctx-bound logger 的日志都带 |
| `user_id` | 鉴权 middleware 跑完之后 | 之前不带(合理,此时还没鉴权) |
| `request_id` / `device_msg_src` | LoggerMiddleware 跑完之后 | 之前不带 |

规律:**一个字段什么时候进绑定上下文(ctx / MDC / contextvars),就从那一刻起出现在后面的日志里**。

---

### 1.6 别名映射

存量代码如果已经用了非 canonical 名,可以在配置里映射:

Go (`logger.yaml`):
```yaml
a4x-logger:
  context:
    aliases:
      firmware_version:
        - firmwareId
        - fw_version
      serial_number:
        - deviceSn
```

Java (`application.yml`):
```yaml
a4x-logger:
  context:
    aliases:
      user_id:
        - userId
        - uid
      firmware_version:
        - firmwareVersion
        - firmwareId
```

Python (`setup_logging` 参数或 `logger.yaml`):
```python
setup_logging(
    "my-service",
    alias_mapping={
        "user_id": ["userId", "uid"],
        "firmware_version": ["firmwareVersion", "fw_version"],
    },
)
```

**只用于存量代码兼容,新代码直接用 canonical 名**。

---

### 1.7 LoggerMiddleware 绑定决策规则

```
IF service 是面向用户服务      → 绑 user_id(从鉴权 ctx / MDC / contextvars 读)
IF service 是多租户 SaaS       → 绑 tenant_id
IF service 涉及 IoT 设备       → 绑 serial_number / model_no / firmware_version
IF service 是服务端 HTTP 服务  → 绑 device_msg_src="cloud"
ALWAYS                        → 绑 request_id(header 透传或生成)
```

不需要的字段**直接不出现**在绑定代码里,**不要**写 `zap.Skip()` / `MDC.put("x","")` / `bind_contextvars(x=None)` —— 反模式,让日志查询变难。

---

## Part 2:IDTYPE Tag(6 种 PII 标签 + 手动 API)

IDTYPE 是 SDK 的合规核心:把 PII 字段值包装成 `[IDTYPE:<type>:<value>]` token,下游日志平台用这个 token 做审计识别、脱敏解码、合规 audit trail。

**SSOT**:`spec/sensitive_types.json`(权威枚举)。以下是 6 个变体的三语速查。

---

### 2.1 `user_id` — 用户 ID

| 语言 | 常量 / 标注 | 包装后示例 |
|---|---|---|
| Go | `sensitive.UserID` | `[IDTYPE:user_id:12345]` |
| Java | `IDType.USER_ID` | `[IDTYPE:user_id:12345]` |
| Python | `"user_id"` (metadata value) | `[IDTYPE:user_id:12345]` |

**struct 标注写法**:

Go:
```go
type CreateOrderRequest struct {
    UserID int64 `json:"userId" sensitive:"user_id"`
}
```

Java:
```java
public class CreateOrderRequest {
    @SensitiveField(IDType.USER_ID)
    private Long userId;
}
```

Python:
```python
from dataclasses import dataclass, field

@dataclass
class CreateOrderRequest:
    user_id: int = field(metadata={"sensitive": "user_id"})
```

**手动 API(只有标量时)**:

Go:
```go
import "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go/sensitive"

log.Info("login user_id=%v", sensitive.FormatIDType(sensitive.UserID, userID))
// → "login user_id=[IDTYPE:user_id:12345]"
```

Java:
```java
import com.a4x.logger.core.sensitive.SensitiveFormatter;

log.info("login user_id={}", SensitiveFormatter.format(IDType.USER_ID, userId));
// → "login user_id=[IDTYPE:user_id:12345]"
```

Python:
```python
from a4x_logger.sensitive import format_idtype

log.info("login user_id=%s", format_idtype("user_id", user_id))
# → "login user_id=[IDTYPE:user_id:12345]"
```

---

### 2.2 `email` — 电子邮件

| 语言 | 常量 / 标注 | 包装后示例 |
|---|---|---|
| Go | `sensitive.Email` | `[IDTYPE:email:alice@a4x.ai]` |
| Java | `IDType.EMAIL` | `[IDTYPE:email:alice@a4x.ai]` |
| Python | `"email"` | `[IDTYPE:email:alice@a4x.ai]` |

**struct 标注写法**:

Go:
```go
type User struct {
    Email string `json:"email" sensitive:"email"`
}
```

Java:
```java
public class User {
    @SensitiveField(IDType.EMAIL)
    private String email;
}
```

Python:
```python
@dataclass
class User:
    email: str = field(metadata={"sensitive": "email"})
```

**手动 API**:

Go:
```go
log.Info("register email=%v", sensitive.FormatIDType(sensitive.Email, emailStr))
```

Java:
```java
log.info("register email={}", SensitiveFormatter.format(IDType.EMAIL, email));
```

Python:
```python
log.info("register email=%s", format_idtype("email", email))
```

---

### 2.3 `device_sn` — 设备序列号

| 语言 | 常量 / 标注 | 包装后示例 |
|---|---|---|
| Go | `sensitive.DeviceSN` | `[IDTYPE:device_sn:SN20260001]` |
| Java | `IDType.DEVICE_SN` | `[IDTYPE:device_sn:SN20260001]` |
| Python | `"device_sn"` | `[IDTYPE:device_sn:SN20260001]` |

**struct 标注写法**:

Go:
```go
type DeviceInfo struct {
    SerialNumber string `json:"serialNumber" sensitive:"device_sn"`
}
```

Java:
```java
public class DeviceInfo {
    @SensitiveField(IDType.DEVICE_SN)
    private String serialNumber;
}
```

Python:
```python
@dataclass
class DeviceInfo:
    serial_number: str = field(metadata={"sensitive": "device_sn"})
```

**手动 API**:

Go:
```go
log.Info("device_bind sn=%v", sensitive.FormatIDType(sensitive.DeviceSN, sn))
```

Java:
```java
log.info("device_bind sn={}", SensitiveFormatter.format(IDType.DEVICE_SN, sn));
```

Python:
```python
log.info("device_bind sn=%s", format_idtype("device_sn", sn))
```

---

### 2.4 `device_mac` — 设备 MAC 地址

| 语言 | 常量 / 标注 | 包装后示例 |
|---|---|---|
| Go | `sensitive.DeviceMAC` | `[IDTYPE:device_mac:AA:BB:CC:DD:EE:FF]` |
| Java | `IDType.DEVICE_MAC` | `[IDTYPE:device_mac:AA:BB:CC:DD:EE:FF]` |
| Python | `"device_mac"` | `[IDTYPE:device_mac:AA:BB:CC:DD:EE:FF]` |

**struct 标注写法**:

Go:
```go
type DeviceInfo struct {
    MAC string `json:"mac" sensitive:"device_mac"`
}
```

Java:
```java
public class DeviceInfo {
    @SensitiveField(IDType.DEVICE_MAC)
    private String mac;
}
```

Python:
```python
@dataclass
class DeviceInfo:
    mac: str = field(metadata={"sensitive": "device_mac"})
```

**手动 API**:

Go:
```go
log.Info("device_seen mac=%v", sensitive.FormatIDType(sensitive.DeviceMAC, macAddr))
```

Java:
```java
log.info("device_seen mac={}", SensitiveFormatter.format(IDType.DEVICE_MAC, mac));
```

Python:
```python
log.info("device_seen mac=%s", format_idtype("device_mac", mac))
```

---

### 2.5 `ticket_id` — 工单 ID

| 语言 | 常量 / 标注 | 包装后示例 |
|---|---|---|
| Go | `sensitive.TicketID` | `[IDTYPE:ticket_id:TK-001]` |
| Java | `IDType.TICKET_ID` | `[IDTYPE:ticket_id:TK-001]` |
| Python | `"ticket_id"` | `[IDTYPE:ticket_id:TK-001]` |

**struct 标注写法**:

Go:
```go
type SupportRequest struct {
    TicketID string `json:"ticketId" sensitive:"ticket_id"`
}
```

Java:
```java
public class SupportRequest {
    @SensitiveField(IDType.TICKET_ID)
    private String ticketId;
}
```

Python:
```python
@dataclass
class SupportRequest:
    ticket_id: str = field(metadata={"sensitive": "ticket_id"})
```

**手动 API**:

Go:
```go
log.Info("ticket_created id=%v", sensitive.FormatIDType(sensitive.TicketID, tid))
```

Java:
```java
log.info("ticket_created id={}", SensitiveFormatter.format(IDType.TICKET_ID, ticketId));
```

Python:
```python
log.info("ticket_created id=%s", format_idtype("ticket_id", ticket_id))
```

---

### 2.6 `user_sn` — 用户序列号

| 语言 | 常量 / 标注 | 包装后示例 |
|---|---|---|
| Go | `sensitive.UserSN` | `[IDTYPE:user_sn:USN-001]` |
| Java | `IDType.USER_SN` | `[IDTYPE:user_sn:USN-001]` |
| Python | `"user_sn"` | `[IDTYPE:user_sn:USN-001]` |

**struct 标注写法**:

Go:
```go
type UserProfile struct {
    UserSN string `json:"userSn" sensitive:"user_sn"`
}
```

Java:
```java
public class UserProfile {
    @SensitiveField(IDType.USER_SN)
    private String userSn;
}
```

Python:
```python
@dataclass
class UserProfile:
    user_sn: str = field(metadata={"sensitive": "user_sn"})
```

**手动 API**:

Go:
```go
log.Info("user_verified sn=%v", sensitive.FormatIDType(sensitive.UserSN, userSN))
```

Java:
```java
log.info("user_verified sn={}", SensitiveFormatter.format(IDType.USER_SN, userSn));
```

Python:
```python
log.info("user_verified sn=%s", format_idtype("user_sn", user_sn))
```

---

## Part 3:主路径 — struct + tag + 占位符(三语)

**推荐的主路径**:SDK 自动反射 tag,业务代码最少。

Go:
```go
type CreateOrderRequest struct {
    UserID       int64  `json:"userId" sensitive:"user_id"`
    Email        string `json:"email" sensitive:"email"`
    SerialNumber string `json:"serialNumber" sensitive:"device_sn"`
    Amount       int64  `json:"amount"`          // 非 PII 不加
}

// 业务代码:
log.Info("order_received %v", req)
// SDK 自动反射 struct tag,对 sensitive 字段包装成 [IDTYPE:...:...] token
// → message: order_received {"userId":"[IDTYPE:user_id:12345]","email":"[IDTYPE:email:alice@a4x.ai]",...}
```

Java:
```java
public class CreateOrderRequest {
    @SensitiveField(IDType.USER_ID)
    private Long userId;

    @SensitiveField(IDType.EMAIL)
    private String email;

    @SensitiveField(IDType.DEVICE_SN)
    private String serialNumber;

    private Long amount;   // 非 PII 不加
}

// 业务代码:
log.info("order_received {}", req);
// Jackson 序列化时反射 @SensitiveField 注解,IDTYPE 自动包装
```

Python:
```python
from dataclasses import dataclass, field

@dataclass
class CreateOrderRequest:
    user_id: int = field(metadata={"sensitive": "user_id"})
    email: str = field(metadata={"sensitive": "email"})
    serial_number: str = field(metadata={"sensitive": "device_sn"})
    amount: int = 0   # 非 PII 不加

# 业务代码:
logger.info("order_received %s", req)
# SDK processor 反射 dataclass field metadata,IDTYPE 自动包装
```

**禁止写法**(绕过反射路径,IDTYPE 失效):

Go:
```go
log.Info(fmt.Sprintf("received order %v", req))  // ❌ Sprintf 先执行,SDK 看不到 struct
log.Info("received order " + req.String())        // ❌ String() 已把 struct 变字符串
```

Java:
```java
log.info("received order " + req.toString());     // ❌ 字符串拼接,IDTYPE 失效
log.info(String.format("received order %s", req));// ❌ format 先执行,SDK 看不到注解
```

Python:
```python
logger.info(f"received order {req}")              # ❌ f-string,SDK 入口前 req 已变字符串
logger.info("received order {}".format(req))      # ❌ .format(),同理
logger.info("received order " + str(req))         # ❌ 拼接,同理
```

---

## Part 4:接入时的 tag 决策规则

```
1. 这个 struct / POJO / dataclass 有 PII 字段吗?(看字段名和业务含义)
   - 没有 → 不加 tag
   - 有 → 继续

2. 业务代码打日志时能用占位符把整个 struct 传进去吗?
   - 能(绝大多数情况)
     → Go:    加 sensitive tag,用主路径(%v + struct)
     → Java:  加 @SensitiveField,用主路径({} + POJO)
     → Python:加 metadata={"sensitive": ...},用主路径(%s + dataclass)
   - 不能(循环里只有散 ID / 必须用 zap.Any / 只有标量)→ 用手动 API

3. 手动 API 选哪个?
   - 只有标量没 struct
     → Go:    sensitive.FormatIDType(sensitive.XXX, value)
     → Java:  SensitiveFormatter.format(IDType.XXX, value)
     → Python: format_idtype("<idtype>", value)
   - 有 struct 必须走 zap.Any(Go 特有场景)
     → Go: sensitive.Wrap(obj)
```

---

## Part 5:PII 扫描关键字(供 pii-scanner.md 用)

当 [pii-scanner.md](go/pii-scanner.md) 在业务代码里扫"裸 PII"时,关键字对照表:

| canonical | 变体(case-insensitive grep) |
|---|---|
| `user_id` | `user_id` / `userId` / `userID` / `uid` / `user-id` |
| `email` | `email` / `mail` / `email_addr` / `emailAddr` |
| `device_sn` | `device_sn` / `serialNumber` / `serial_number` / `sn=` / `deviceSn` |
| `device_mac` | `device_mac` / `mac` / `macAddr` / `mac_addr` |
| `ticket_id` | `ticket_id` / `ticketId` / `tid` / `ticket-id` |
| `user_sn` | `user_sn` / `userSn` / `userSerial` / `user-sn` |
