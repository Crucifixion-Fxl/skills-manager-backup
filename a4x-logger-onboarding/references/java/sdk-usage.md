# SDK Usage — Java

> 本文件教 **`a4x-logger-sdk` Java 版怎么正确用**(接入步骤 / MDC 约定 / `@SensitiveField` / OTel bridge / SDK 行为承诺 / 测试捕获)。不教怎么 recon 项目(看 [../project-recon.md](../project-recon.md))也不教接入流程(看 [execution.md](execution.md))。
>
> **基准版本**:`a4x-logger-starter` 1.0.0 / JDK 11 / Spring Boot 2.5.5 ~ 2.7.x
>
> **SDK 权威文档在** `<sdk>/docs/architecture/java/usage.md`。本文件是 **skill 侧精简版** + **每条内容都标注 verify 入口**(agent 引用前必须能在 SDK 仓库 grep / read 到)。

---

## §0 Verify 入口

### 包名 → 目录映射

| Maven 模块 | 源码目录 | 主要内容 |
|---|---|---|
| `a4x-logger-core` | `<sdk>/java/a4x-logger-core/src/main/java/com/a4x/logger/core/` | `@SensitiveField` 注解 / `IDType` 枚举 / `MaskConfig` / `RequestId` |
| `a4x-logger-format` | `<sdk>/java/a4x-logger-format/src/main/java/com/a4x/logger/format/` | `SensitiveInfoConverter` / `CanonicalContextJsonProvider` / `ServiceInstanceJsonProvider` / `DynamicFieldsJsonProvider` |
| `a4x-logger-starter` | `<sdk>/java/a4x-logger-starter/src/main/java/com/a4x/logger/starter/` | `A4xLoggerAutoConfiguration` / `A4xLoggerInitEmitter` / `A4xLoggerProperties` |
| `a4x-logger-reference-app` | `<sdk>/java/a4x-logger-reference-app/` | 端到端 L3 测试 fixture;`LogCapture` 通过 test-jar 复用 |

### Verify 入口表

| 声称类型 | 命令 |
|---|---|
| `@SensitiveField` 注解定义 | `Grep 'public @interface SensitiveField' --include=*.java <sdk>/java/a4x-logger-core/src/main/java/` |
| `IDType` 枚举值 | `Read <sdk>/java/a4x-logger-core/src/main/java/com/a4x/logger/core/enums/IDType.java` |
| `A4xLoggerAutoConfiguration` 类 | `Grep 'class A4xLoggerAutoConfiguration' --include=*.java <sdk>/java/a4x-logger-starter/src/main/java/` |
| `LogCapture` 工具类 | `Read <sdk>/java/a4x-logger-format/src/test/java/com/a4x/logger/format/test/LogCapture.java` |
| `RequestId.newRequestId()` | `Grep 'newRequestId' --include=*.java <sdk>/java/a4x-logger-core/src/main/java/` |
| Canonical 字段 / null-omit 契约 | `Read <sdk>/spec/log_format.json` |
| IDTYPE 变体(6 种) | `Read <sdk>/spec/sensitive_types.json` |

### 捷径

首次接入任务 Read `<sdk>/docs/architecture/java/usage.md` + `<sdk>/spec/log_format.json` 各一次,80% 引用都覆盖到。

---

## §1 5 分钟接入

Spring Boot 项目 3 步接入,业务代码零改动。

### Step 1:加 Maven 依赖

```xml
<dependency>
    <groupId>com.a4x.logger</groupId>
    <artifactId>a4x-logger-starter</artifactId>
    <version>1.0.0</version>
</dependency>
```

> **非 Spring 项目**:引 `a4x-logger-format` + `a4x-logger-core` + `logback-classic`,见 `<sdk>/docs/architecture/java/usage.md §8`。
>
> **版本权威**:具体推荐版本以 `<sdk>/docs/architecture/version-matrix.md §2.2` 为准;本文件示例版本可能滞后,接入步骤不会变。

### Step 2:写 `application.yml`

```yaml
a4x-logger:
  service-name: my-service       # 默认读 SERVICE_NAME env;两源都缺失时字段不出现
  instance: ${HOSTNAME}          # 不要写 ${HOSTNAME:unknown}!:- 会让 null-omit 失效

  level: INFO
  max-message-length: 32768      # 默认 32768(32KB),最小 256

  sensitive:
    enabled: true
    on-error: log_raw            # masking 抛异常时的 fallback:log_raw / redact / drop
    masks:                       # SDK 不内置任何规则,需显式配(label-driven 写法见 §2)
      phone:
        - 'phone:\s*([^,\s"$]+)'
        - 'phone=([^,\s"$]+)'
        - 'phone":"([^,\s"$]+)'

  context:
    aliases:                     # 旧项目迁移期:canonical key 不在 MDC 时走 alias 回退
      user_id:
        - userId
        - uid
      firmware_version:
        - firmwareVersion
        - firmwareId
```

### Step 3:打日志

任何 Spring 组件里直接用标准 SLF4J:

```java
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

private static final Logger log = LoggerFactory.getLogger(UserService.class);

log.info("查询用户 {}", id);              // 基本用法
log.info("创建用户 {}", user);            // user 是 POJO,@SensitiveField 字段自动 IDTYPE 包装
log.error("数据库连接失败", e);           // throwable 自动抽到 exception 字段
```

**启动时 `logger_init` 事件**:SDK 自动 emit 一条结构化事件(ADR-10),含 `event="logger_init"` / `sdk_version` / `schema_version` / `language="java"` / `service` / `instance`。这不是 bug,是 observability 平台关联业务日志 schema 版本用的。

> **完整可跑示例**:`<sdk>/examples/java-spring-demo/`(9 个 curl 场景验证 SDK 全部能力,UserController.java 含 8 个核心 + 1 个 DFC 多字段场景;README 列了 8 个核心)。

---

## §2 MDC 写入方约定

SDK **不负责 MDC 的写入和清理**,仅在日志格式化时从 MDC 读取 canonical 字段。SDK 对所有写入方同等对待。

**两件 SDK 硬要求**:

1. **MDC key 必须用 canonical 名**(或预先在 `application.yml` 里配过 alias):
   - `MDC.put("user_id", ...)` ✅
   - `MDC.put("uid", ...)` 但没配 alias ❌(被 SDK Format 层过滤掉,JSON 不出现)

2. **请求结束时必须有人 `MDC.clear()`**:Servlet 容器线程会被复用,上一个请求的字段残留会被下一个请求继承,**真出过事故**。

> **Tomcat 线程复用事故场景**:Tomcat 线程 #5 处理请求 A,Filter 跑完 `MDC.put("user_id","alice")`;Controller 内部抛异常被全局异常处理器吞掉但**没 clear MDC**;Tomcat 立即把 #5 复用给请求 B,B 的 Filter 因某条件分支没 put `user_id`;此时 B 内部 `log.info` 拿到的依然是 `user_id=alice`。教训:`MDC.clear()` 必须放 **`finally`**,不是 `try` 末尾。

**12 个 canonical MDC 字段**:

| 字段 | 分类 | 写入方 |
|---|---|---|
| `trace_id` | Tracing | OTel Java Agent(自动)或业务 Filter |
| `span_id` | Tracing | OTel Java Agent(自动) |
| `request_id` | Tracing | 业务 Filter(UUID 兜底) |
| `user_id` | Context | 业务 Filter(从 JWT/header 解析) |
| `tenant_id` | Context | 多租户项目必绑 |
| `account_id` | Context | 按需 |
| `serial_number` | Context | IoT 项目必绑 |
| `model_no` | Context | IoT 项目必绑 |
| `firmware_version` | Context | IoT 项目必绑 |
| `device_msg_src` | Context | 取值 `cloud`/`device`/`app`/`ble`/`mqtt` |
| `session_id` | Context | 特定业务流程场景手动绑 |
| `task_name` | Context | `@Scheduled` / 批处理入口手动 put |

> ⚠️ **`trace_id` / `span_id` 别管**。接 OTel 后(Java Agent bytecode 方式或 OTel SDK + `opentelemetry-logback-mdc` bridge),这俩自动写 MDC,业务代码不要再 `MDC.put` 它们。

> ⚠️ **非 canonical 的 MDC key**(如 `MDC.put("order_id", ...)`)被 SDK 过滤掉,JSON 不出现。请求级业务字段走 `StructuredArguments.kv()` 动态字段,见 §5。

**同字段单写入方红线**:下面三种典型写法任选,**但同一个字段只能有一个写入方**(避免双写覆盖)。

### 写法 A:项目已有 AuthFilter / JwtFilter(中型项目最常见)

直接在自家 Filter 里 `MDC.put`,**不要再加**额外的 `LoggingContextFilter`:

```java
public class JwtFilter implements Filter {
    public void doFilter(ServletRequest req, ServletResponse res, FilterChain chain)
            throws IOException, ServletException {
        try {
            HttpServletRequest http = (HttpServletRequest) req;
            Claims claims = jwtParser.parse(http.getHeader("Authorization"));
            MDC.put("user_id",   claims.getSubject());
            MDC.put("tenant_id", claims.get("tenant", String.class));

            // request_id:有 header 就用,没有就 SDK helper 兜底(32-hex 无连字符)
            String reqId = http.getHeader("X-Request-Id");
            MDC.put("request_id", (reqId != null) ? reqId : RequestId.newRequestId());

            chain.doFilter(req, res);
        } finally {
            MDC.clear();   // ⚠️ 必须 finally,严防线程池复用串号
        }
    }
}
```

`RequestId.newRequestId()` — `com.a4x.logger.core.context.RequestId`,返回 32-hex 无连字符(与 W3C trace_id 同形态)。

### 写法 B:网关 / Mesh 注入 header,服务内只做 header → MDC 映射

```java
import com.a4x.logger.core.context.RequestId;
import org.slf4j.MDC;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import javax.servlet.*;
import javax.servlet.http.HttpServletRequest;
import java.io.IOException;

public class LoggingContextFilter implements Filter {
    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        HttpServletRequest req = (HttpServletRequest) request;
        try {
            // 只在 header 真有值时 put;header 缺失就不写,SDK 输出中该字段直接 omit
            // trace_id / span_id 由 OTel 接管,本 Filter 不动
            putIfPresent(req, "X-User-Id",   "user_id");
            putIfPresent(req, "X-Tenant-Id", "tenant_id");

            // request_id 是唯一例外:每条业务日志最好都有,缺失时用 SDK helper 兜底
            String reqId = req.getHeader("X-Request-Id");
            MDC.put("request_id", (reqId != null) ? reqId : RequestId.newRequestId());

            chain.doFilter(request, response);
        } finally {
            MDC.clear();
        }
    }

    private void putIfPresent(HttpServletRequest req, String headerName, String mdcKey) {
        String v = req.getHeader(headerName);
        if (v != null) MDC.put(mdcKey, v);
    }
}

@Configuration
class FilterConfig {
    @Bean
    public FilterRegistrationBean<LoggingContextFilter> loggingContextFilter() {
        FilterRegistrationBean<LoggingContextFilter> reg =
            new FilterRegistrationBean<>(new LoggingContextFilter());
        reg.addUrlPatterns("/*");
        return reg;
    }
}
```

> **两个细节**:(1) 用 `FilterRegistrationBean` 显式注册而非 `@Component`——MockMvc 测试环境下更稳;(2) 不要写 `setOrder(...)`——默认值在 OTel Filter 之后,顺序正确。

### 写法 C:定时任务 / 批处理(无 HTTP 上下文)

```java
@Scheduled(fixedRate = 60000)
public void syncData() {
    MDC.put("task_name", "syncData");
    MDC.put("request_id", RequestId.newRequestId());
    try {
        log.info("开始同步数据");
        // 业务逻辑...
    } finally {
        MDC.remove("task_name");
        MDC.remove("request_id");
    }
}
```

---

## §3 IDTYPE / `@SensitiveField`

### 注解定义

```java
// 完整路径:com.a4x.logger.core.annotation.SensitiveField
// Verify: Read <sdk>/java/a4x-logger-core/src/main/java/com/a4x/logger/core/annotation/SensitiveField.java

import com.a4x.logger.core.annotation.SensitiveField;
import com.a4x.logger.core.enums.IDType;
import com.a4x.logger.core.jackson.SensitiveAwareObjectMapper;
import com.fasterxml.jackson.core.JsonProcessingException;

public class User {
    @SensitiveField(IDType.USER_ID)
    private String id;

    @SensitiveField(IDType.EMAIL)
    private String email;

    private String name;        // 无注解,原样输出

    @JsonIgnore
    private String password;    // @JsonIgnore 字段在 Jackson introspection 阶段已剔除,绝不出现

    /**
     * 必须 override toString() 并委托给 LOG_INSTANCE。
     * SLF4J {} 占位符调的是 toString(),不是单独 serialize——
     * 不 override 则 @SensitiveField 等同无效(默认 toString / Lombok @ToString 全是裸值)。
     *
     * 如果使用 Lombok @ToString,需在类级别 exclude 全部字段后手写此方法,
     * 否则 Lombok 生成的 toString 会被调用,绕开 LOG_INSTANCE。
     */
    @Override
    public String toString() {
        try {
            return SensitiveAwareObjectMapper.LOG_INSTANCE.writeValueAsString(this);
        } catch (JsonProcessingException e) {
            return "<serialization-error>";
        }
    }
}
```

### IDType 变体清单

Verify:`Read <sdk>/java/a4x-logger-core/src/main/java/com/a4x/logger/core/enums/IDType.java`(与 `spec/sensitive_types.json` 双向权威)。

| 枚举值 | 输出形式 |
|---|---|
| `IDType.USER_ID` | `[IDTYPE:user_id:12345]` |
| `IDType.EMAIL` | `[IDTYPE:email:alice@a4x.ai]` |
| `IDType.DEVICE_SN` | `[IDTYPE:device_sn:SN-ABC-123]` |
| `IDType.DEVICE_MAC` | `[IDTYPE:device_mac:AA:BB:CC:DD:EE:FF]` |
| `IDType.TICKET_ID` | `[IDTYPE:ticket_id:TK-001]` |
| `IDType.USER_SN` | `[IDTYPE:user_sn:USN-789]` |

**编译期安全**:写错 IDType 名字 = 编译失败(强类型枚举)。比 Go 的 struct tag(运行时字符串,typo silent fallback)更稳。

### Jackson 反射闭环

**真正的机制**:SLF4J `{}` 占位符替换调的是 `user.toString()`(不是把 `user` 单独 serialize 给 Jackson)。所以业务 POJO 必须 **override `toString()` 委托给 `SensitiveAwareObjectMapper.LOG_INSTANCE`**,IDTYPE 包装才会生效。否则默认 `toString()`(或 Lombok `@ToString`)走的是裸字段值,`@SensitiveField` 等同无效。

当 `log.info("user {}", user)` 调 `user.toString()` 时,**如果 toString 走的是 `SensitiveAwareObjectMapper.LOG_INSTANCE`**,IDTYPE 自动生效(来源:SDK `docs/architecture/java/usage.md` §6.1):

```java
log.info("user {}", user);
// → message: "user {\"id\":\"[IDTYPE:user_id:12345]\",\"email\":\"[IDTYPE:email:alice@a4x.ai]\",\"name\":\"alice\"}"
```

### 嵌套 POJO / 集合 / Map corner case

| 情况 | 行为 |
|---|---|
| 嵌套 POJO(`User.address` 是另一个 POJO) | Jackson 递归序列化,嵌套 POJO 里的 `@SensitiveField` 同样生效 |
| `List<User>` | Jackson 序列化每个元素,注解生效 |
| `Map<String, User>` | value 是 POJO 时注解生效;key 是字符串,不走 IDTYPE |
| 标量字段(无注解) | 原样序列化,不走 IDTYPE |
| `@JsonIgnore` 字段 | introspection 阶段已剔除,绝不出现在 JSON |

**禁止预格式化 PII**(对应 `spec/log_format.json emission_policy.placeholder_input_invariant`):

```java
log.info("user " + user.toString());         // ❌ 字符串拼接,IDTYPE 反射失效,PII 明文落盘
log.info(String.format("user %s", user));    // ❌ 同上
log.info("user {}", user);                   // ✅ 正解:SDK 拿到原始对象才能反射 @SensitiveField
```

一旦调用方提前拼字符串,SLF4J 拿到的已是 final string,SDK 后续没有任何反射 / Jackson 切口——物理上无法补救,只能靠 code review 和静态规则约束。

---

## §4 OTel logback bridge

### 接线方式

**OTel Java Agent**(bytecode 方式,最常见):在 Servlet 容器层自动把当前 Span 的 `trace_id` / `span_id` 写入 MDC——根本不进 Spring Filter chain。业务代码**什么都不用做**,只要部署了 OTel Java Agent,两个字段自动出现在日志 JSON 里。

**OTel SDK + logback bridge**(非 Agent 方式):需要在 `logback-spring.xml` 里挂 `OpenTelemetryAppender`:

```xml
<!-- logback-spring.xml — Tier 2 形态 -->
<configuration>
    <include resource="a4x-logger-base.xml"/>

    <!-- OTel logback bridge:把当前 Span context 写入 MDC -->
    <appender name="OpenTelemetry"
              class="io.opentelemetry.instrumentation.logback.mdc.v1_0.OpenTelemetryAppender">
        <appender-ref ref="CONSOLE"/>   <!-- CONSOLE 来自 SDK base.xml -->
    </appender>

    <root level="info">
        <appender-ref ref="OpenTelemetry"/>
    </root>
</configuration>
```

Maven 依赖(版本跟项目 OTel BOM 对齐,`<sdk>/docs/architecture/version-matrix.md` 有推荐版本):

```xml
<dependency>
    <groupId>io.opentelemetry.instrumentation</groupId>
    <artifactId>opentelemetry-logback-mdc-1.0</artifactId>
</dependency>
```

### trace_id / span_id 自动写 MDC

无论哪种 OTel 方式,一旦装好:
- `trace_id` / `span_id` 由 OTel 自动写入 MDC,SDK 从 MDC 读出输出 JSON
- 业务代码**不要**再 `MDC.put("trace_id", ...)` 或 `MDC.put("span_id", ...)`

### Java Agent vs OTel SDK 取舍

| 方式 | 优势 | 劣势 |
|---|---|---|
| **Java Agent**(推荐) | 零代码改动,自动 instrument Servlet / gRPC / Kafka 等 | 需 JVM 启动参数 `-javaagent` |
| **OTel SDK + logback bridge** | 纯 Maven 依赖,不改启动参数 | 只做 logback bridge,Servlet 等入口需手动 instrument |

没有 OTel 时:SDK 照常工作,`trace_id` / `span_id` 字段按 null-omit 规则不出现,不影响脱敏和 JSON 格式化。

---

## §5 SDK 行为承诺

### null-omit

MDC 中某个 canonical key 不存在(或被 `MDC.remove()`)时,SDK JSON 输出中该字段**不出现**(key 直接省略,不输出 null / 空字符串)。依据 `spec/log_format.json emission_policy.unified_rule`。

```java
// trace_id 未写 MDC → 日志 JSON 里没有 trace_id 字段
MDC.put("user_id", "u-100");
log.info("处理请求");
// → {"level":"INFO","message":"处理请求","user_id":"u-100","service":"my-service",...}
//   没有 trace_id / span_id / tenant_id 等未写入 MDC 的字段
```

`""` 是合法值:显式 `MDC.put("user_id", "")` 表示"已确认匿名",SDK 仍输出 `"user_id": ""`。

**`application.yml` 陷阱**:`instance: ${HOSTNAME:unknown}` 中的 `:-unknown` 会让 null-omit 失效(`instance` 变成 `"unknown"` 而非不出现)。正确写法:`instance: ${HOSTNAME}`。

### placeholder_input_invariant(`{}` 占位符)

Java 用 SLF4J `{}` 占位符。SDK 必须看到原始对象才能反射 `@SensitiveField`。对应 `spec/log_format.json emission_policy.placeholder_input_invariant`。

```java
log.info("user {}", user);         // ✅ SDK 看到 user 对象,IDTYPE 反射生效
log.info("user " + user);          // ❌ SDK 只看到字符串,IDTYPE 失效,PII 明文落盘
```

### 动态字段(`StructuredArguments.kv`)

请求级业务自定义字段通过 `StructuredArguments.kv()` 传入,每次 log 调用显式传:

```java
import static net.logstash.logback.argument.StructuredArguments.kv;

log.info("处理订单", kv("order_id", "ORD-001"), kv("amount", 99.9));
// → JSON 顶层多两个字段: "order_id":"ORD-001", "amount":99.9
```

**DFC(Dynamic Field Conflict)**:动态字段名落在 SDK-managed 20 字段集合里时静默丢弃:

```java
log.info("test",
    kv("order_id", "ORD-001"),   // ✅ 透传
    kv("trace_id", "fake"),      // ❌ DFC 静默丢弃(reserved)
    kv("level", "CRITICAL"));    // ❌ DFC 静默丢弃(reserved)
```

### `logger_init` 事件(ADR-10)

`a4x-logger-starter` 启动时自动 emit 一次。格式:

```json
{
  "timestamp": "2026-04-28T07:53:41.664Z",
  "level": "INFO",
  "logger": "c.a.l.s.a.A4xLoggerInitEmitter",
  "message": "logger_init",
  "event": "logger_init",
  "sdk_version": "1.0.0",
  "schema_version": "1.1.0",
  "language": "java",
  "service": "my-service",
  "instance": "pod-abc"
}
```

observability 平台靠 `service + instance` 把这条事件与后续业务日志关联,反查 schema 版本。**业务日志体不带 `schema_version` 字段**。starter 项目**不要手动再 emit** `logger_init`——会重复。

### panic safety(ADR-15)

SDK 承诺:**任何 SDK 内部 bug 或业务对象序列化抛异常,都不会让业务进程崩溃**。

| 失败位置 | SDK 行为 |
|---|---|
| `SensitiveInfoConverter.convert` 最外层 | 不抛异常,`message` 字段值为 `[a4x_logger internal error: <ClassName>]` |
| `LogMaskUtil.applyMasking` 抛异常 | 按 `on-error` 配置 fallback(log_raw / redact / drop) |
| IDTYPE 反射期(Jackson 序列化)抛异常 | 按 `on-error` fallback,跳过 IDTYPE 包装,raw 值原样输出 |
| `CanonicalContextJsonProvider` 单字段抛 | 该字段在该条 JSON 中省略,其它字段照常输出 |
| 业务对象 `toString` 抛异常 | SLF4J safeObjectAppend 兜底,message 含 `[FAILED toString()]` |

---

## §6 测试捕获

### 工具类:`LogCapture`

来源:`<sdk>/java/a4x-logger-format/src/test/java/com/a4x/logger/format/test/LogCapture.java`

通过 test-jar 依赖复用(参考 `<sdk>/java/a4x-logger-reference-app/pom.xml`):

```xml
<dependency>
    <groupId>com.a4x.logger</groupId>
    <artifactId>a4x-logger-format</artifactId>
    <version>1.0.0</version>
    <type>test-jar</type>
    <classifier>tests</classifier>
    <scope>test</scope>
</dependency>
```

**前提**:测试资源里的 `logback-test.xml` 必须 `<include resource="a4x-logger-base.xml"/>` —— `LogCapture` 借用 `CONSOLE` appender 的 encoder 做 JSON encode,缺失则 `IllegalStateException`。

`LogCapture` 是 `AutoCloseable`,使用 try-with-resources 模式——不需要 JUnit 5 `@RegisterExtension`。

### 用法:try-with-resources

```java
import com.a4x.logger.format.test.LogCapture;
import com.fasterxml.jackson.databind.JsonNode;
import static org.assertj.core.api.Assertions.assertThat;

@Test
void queryUser_emitsCanonicalFields() {
    try (LogCapture cap = LogCapture.start()) {
        MDC.put("user_id", "u-100");
        MDC.put("request_id", "550e8400e29b41d4a716446655440000");
        try {
            log.info("查询用户 {}", 42);
        } finally {
            MDC.clear();
        }

        JsonNode line = cap.lastJsonLine();
        assertThat(line.get("level").asText()).isEqualTo("INFO");
        assertThat(line.get("message").asText()).isEqualTo("查询用户 42");
        assertThat(line.get("user_id").asText()).isEqualTo("u-100");
    }
}
```

### 断言 API

| 方法 | 说明 |
|---|---|
| `LogCapture.start()` | 静态工厂,挂 `ListAppender` 开始捕获 |
| `cap.size()` | 已捕获事件总数 |
| `cap.lastJsonLine()` | 最后一条事件 encode + parse 成 `JsonNode` |
| `cap.jsonAt(int index)` | 第 i 条事件(0-based) |
| `cap.allJsonLines()` | 所有捕获事件的 `List<JsonNode>` |
| `cap.close()` 或 try-with-resources | 摘掉 ListAppender,恢复 root 状态 |

**常用断言模式**:

```java
// 字段存在 + 值
assertThat(line.get("user_id").asText()).isEqualTo("u-100");

// 字段不存在(null-omit)
assertThat(line.has("tenant_id")).isFalse();

// IDTYPE 包装
assertThat(line.get("message").asText())
    .contains("[IDTYPE:user_id:");

// exception 字段
assertThat(line.has("exception")).isTrue();
assertThat(line.get("exception").asText()).contains("RuntimeException");

// masking
assertThat(line.get("message").asText()).isEqualTo("user phone is ***");
```

### Spring Boot 测试(`@SpringBootTest`)模式

Spring 启动期有 `logger_init` / Spring 本身的日志,业务日志在中间或最后。倒序查找目标 message:

```java
@SpringBootTest
@AutoConfigureMockMvc
class E2ETest {
    @Autowired MockMvc mockMvc;

    @Test
    void getUser_emitsExpectedJson() throws Exception {
        try (LogCapture cap = LogCapture.start()) {
            mockMvc.perform(get("/users/42")
                    .header("X-User-Id", "u-100"))
                .andExpect(status().isOk());

            JsonNode line = findBusinessLine(cap, "query user 42");
            assertThat(line.get("user_id").asText()).isEqualTo("u-100");
            // null-omit:未传 header 的 canonical 字段不出现
            assertThat(line.has("tenant_id")).isFalse();
        }
    }

    private static JsonNode findBusinessLine(LogCapture cap, String expectedMessage) {
        for (int i = cap.size() - 1; i >= 0; i--) {
            JsonNode line = cap.jsonAt(i);
            if (line.has("message") && expectedMessage.equals(line.get("message").asText())) {
                return line;
            }
        }
        return null;
    }
}
```

完整实例:`<sdk>/java/a4x-logger-reference-app/src/test/java/com/a4x/logger/refapp/ReferenceAppE2ETest.java`

---

## §7 项目层 convention vs SDK 硬规则

| SDK 硬规则(agent 必须遵守) | 项目层 convention(推荐,migration 不强制) |
|---|---|
| SLF4J `{}` 占位符传原始对象,禁止预格式化 PII | `action=<name> result=<state>` message 格式 |
| `MDC.clear()` 放 `finally`,严防线程复用泄漏 | canonical key 命名统一(`user_id` 不是 `uid`) |
| MDC key 必须是 canonical 名(或配了 alias) | Info/Warn/Error level 选型规范 |
| `@SensitiveField` 标注 POJO 的 PII 字段 | message 里是否加 `duration_ms` |
| DFC:不用 `kv("trace_id", ...)` 覆盖 SDK 管理字段 | action 命名 snake_case verb_noun |
| `instance: ${HOSTNAME}` 不写 `:-unknown` fallback | 是否启用 Actuator 动态日志级别 |
| 非 canonical MDC key 走 `kv()` 动态字段,不走 `MDC.put` | 日志 message 语言(中文 / 英文) |

**项目层 convention** 看 [../log-style-guide.md](../log-style-guide.md)。**migration 时不强行 retrofit 历史代码**,见 [execution.md](execution.md) 的迁移步骤。

---

> **版本提示**:本文件基于 `a4x-logger-starter` 1.0.0。版本升级时以 `<sdk>/docs/architecture/version-matrix.md` 为权威,接入步骤不变。
