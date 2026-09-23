# Java 接入高频坑

已踩过的坑,按影响严重程度排序。每条给:**现象 → 根因 → 修复 → 自检 grep**。

> 读本文前建议先看 [sdk-usage.md](sdk-usage.md)——MDC 写入方约定(§2)、`@SensitiveField` 规则(§3)、OTel bridge(§4)在那里有完整说明;本文只讲"踩坑"，不重复基础用法。

---

### 1. MDC.clear() 没放 finally → 线程复用泄漏

**现象**:某条请求日志里的 `user_id` 不属于这条请求的用户。例如请求 B 的日志里出现 `user_id=alice`，但 alice 对应的是同一个 Tomcat 线程刚处理完的请求 A，B 本身没有 user_id。

**根因**:Tomcat 线程 #5 处理请求 A，Filter 跑完 `MDC.put("user_id","alice")`；Controller 内部抛异常被业务全局异常处理器吞掉但**没有 clear MDC**；Tomcat 立即把线程 #5 复用给请求 B，B 的 Filter 因某条件分支没有 put `user_id`；此时 B 内部 `log.info` 拿到的依然是 `user_id=alice`。

常见触发路径:

- 全局 `@ExceptionHandler` / `@ControllerAdvice` 在 Filter 之外处理异常,把控制权还给 Spring 后 Filter 的 `finally` 没跑
- `MDC.clear()` 写在 `chain.doFilter` 之后而不是 `finally` 块里,异常抛出时直接跳过

**修复**:`MDC.clear()` 必须放 **`finally`** 块,无论 chain 是否抛异常都能跑到:

```java
public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
        throws IOException, ServletException {
    try {
        MDC.put("user_id",   extractUserId(request));
        MDC.put("request_id", extractOrGenRequestId(request));
        chain.doFilter(request, response);
    } finally {
        MDC.clear();   // ✅ 必须 finally,异常路径也能执行
    }
}
```

**自检 grep**:

```bash
# 找 doFilter / afterCompletion 里有 MDC.put 但没有在 finally 块里 MDC.clear 的 Filter
grep -rn 'MDC\.put' --include='*.java' src/ | grep -v test

# 配合人工检查每个含 MDC.put 的类是否有 finally { MDC.clear() }
grep -rn 'MDC\.clear' --include='*.java' src/ | grep -v test
```

---

### 2. Filter 顺序错位 → SDK Filter 在业务 Filter 之后,canonical 字段没写入

**现象**:日志 JSON 里所有请求的 `user_id` / `tenant_id` 都不出现(null-omit),即使请求携带了有效 JWT / header。

**根因**:`LoggingContextFilter`(或自家 Auth Filter)被 Spring 分配了比预期更晚的执行顺序,在 `chain.doFilter` 调用到业务 Controller 时 `MDC.put` 还没跑。典型场景:

- Filter 用 `@Component` 注册,没设置 `Order`,默认顺序恰好在另一个 Filter 之后且该 Filter 消费了 Body(body-consuming Filter 会先跑)
- 多个 Filter 同时用 `@Component` + `@Order`,数字配反(数字越小越早跑)
- 项目同时装了 OTel SDK Spring AutoConfig Filter(默认 `HIGHEST_PRECEDENCE`)和业务 Filter,OTel Filter 先跑此时 MDC 里还没有 `user_id`,OTel bridge 捕获的是空 MDC 快照

**修复**:

```java
// 用 FilterRegistrationBean 显式注册,控制 order 和 url pattern
// LoggingContextFilter 不是 SDK 提供的类,需要业务方自行实现(模板见 sdk-usage.md §2 写法 B)
@Configuration
class FilterConfig {
    @Bean
    public FilterRegistrationBean<LoggingContextFilter> loggingContextFilter() {
        FilterRegistrationBean<LoggingContextFilter> reg =
            new FilterRegistrationBean<>(new LoggingContextFilter());
        reg.addUrlPatterns("/*");
        // 不设 setOrder → 默认 LOWEST_PRECEDENCE - 1
        // 如果需要在 OTel Filter 之后、业务 Filter 之前写入 MDC,显式指定顺序:
        // reg.setOrder(Ordered.LOWEST_PRECEDENCE - 100);
        return reg;
    }
}
```

规则速查(`LoggingContextFilter` = 业务自行实现的类,模板见 [sdk-usage.md §2 写法 B](sdk-usage.md#2-mdc-写入方约定)):

| 场景 | 正确顺序 |
|---|---|
| OTel Java Agent(bytecode) | 不进 Spring Filter chain,无顺序问题 |
| OTel SDK Spring AutoConfig Filter | 最早(`HIGHEST_PRECEDENCE`);LoggingContextFilter 在其后默认即可 |
| 自家 Auth Filter + LoggingContextFilter 分开 | Auth 先跑(解析 JWT),LoggingContextFilter 后跑读 attribute |

**自检 grep**:

```bash
# 列出项目里所有 FilterRegistrationBean 注册,检查是否有 setOrder 冲突
grep -rn 'FilterRegistrationBean\|setOrder\|@Order' --include='*.java' src/ | grep -v test
```

---

### 3. Logback async appender 丢日志 / 错配 discardingThreshold

**现象**:高并发压测或峰值流量时日志出现丢失;或 WARN 以下的日志在 queue 将满时消失(INFO 正常时候有,压力下没有)。

**根因**:Logback `AsyncAppender` 默认 `discardingThreshold=20`——当 queue 剩余容量低于 20% 时,**自动丢弃 TRACE / DEBUG / INFO** 日志。SDK 默认走 console appender(同步),但如果项目在 `logback-spring.xml` 里把 SDK CONSOLE 包进 `AsyncAppender`,这个阈值就会生效。

另一个触发点:`queueSize` 太小(默认 256)且 Appender 下游慢(如 ELK HTTP appender),producer 速度超过 consumer,队列满 → 丢弃。

**修复**:如果业务需要异步 Appender,必须显式关掉丢弃行为:

```xml
<!-- logback-spring.xml -->
<appender name="ASYNC_CONSOLE" class="ch.qos.logback.classic.AsyncAppender">
    <appender-ref ref="CONSOLE"/>        <!-- CONSOLE 来自 SDK base.xml -->
    <queueSize>2048</queueSize>          <!-- 加大队列 -->
    <discardingThreshold>0</discardingThreshold>  <!-- ✅ 关掉自动丢弃 -->
    <neverBlock>false</neverBlock>       <!-- false = 队列满时阻塞 producer(不丢日志但可能慢) -->
    <!-- 如果不能接受阻塞:neverBlock=true + discardingThreshold=0 → 队列满时丢,但不区分级别 -->
</appender>
```

判断标准:

- **合规 / 审计日志**:`discardingThreshold=0` + `neverBlock=false`(宁慢不丢)
- **普通业务日志**:`discardingThreshold=0` + `neverBlock=true` + 足够大 `queueSize`(接受极端峰值下均匀丢弃)

**自检 grep**:

```bash
# 找 AsyncAppender 配置;如果有,检查 discardingThreshold 是否显式设为 0
grep -rn 'AsyncAppender\|discardingThreshold' --include='*.xml' src/main/resources/
```

---

### 4. OTel Java Agent + OTel SDK 双装载 → trace_id 重复 instrument

**现象**:应用日志中 `trace_id` 字段出现两次,或 MDC 里同时存在 `trace_id`(由 OTel Java Agent 写入)和另一个来自 OTel SDK bridge 写入的值,两者格式不同(一个是 W3C 32-hex,另一个是旧格式)。也可能表现为 Span 被计数两次、Kafka / gRPC 自动 instrument 重复触发。

**根因**:OTel Java Agent 通过 bytecode instrumentation 在 Servlet 容器层自动注入 trace context 并写 MDC;OTel SDK(`opentelemetry-logback-mdc-1.0` bridge 或 `opentelemetry-sdk`)是另一套代码路径,两者独立工作但写同一个 MDC key。双装载时:

- `trace_id` / `span_id` 被写两次(后写覆盖先写,非确定性)
- SDK bytecode 拦截 + SDK API 拦截对同一个方法双重 instrument,Span count 翻倍
- JVM 启动慢,日志中出现 OTel SDK 初始化与 Agent 初始化的竞态 WARN

**修复**:二选一,不可同时装:

```
选项 A:纯 Java Agent(推荐)
  ✅ JVM 启动参数加 -javaagent:/path/to/opentelemetry-javaagent.jar
  ❌ pom.xml 里不要引 opentelemetry-logback-mdc-1.0 / opentelemetry-sdk

选项 B:纯 OTel SDK + logback bridge(不改启动参数场景)
  ❌ 不加 -javaagent
  ✅ pom.xml 引 opentelemetry-logback-mdc-1.0
  ✅ logback-spring.xml 挂 OpenTelemetryAppender
  ⚠️  此时只有 logback bridge 生效,Servlet / Kafka / gRPC 自动 instrument 需手动配置
```

**自检 grep**:

```bash
# 检查 pom.xml 是否同时引了 Java Agent 相关的 artifact 和 logback bridge
grep -n 'opentelemetry-logback-mdc\|opentelemetry-sdk\|opentelemetry-javaagent' pom.xml

# 检查启动脚本 / Dockerfile 里是否有 -javaagent
grep -rn 'javaagent' Dockerfile docker-compose.yml .env* k8s/ || true
```

---

### 5. Lombok @ToString 把 PII 字段当裸值打

**现象**:POJO 上已标 `@SensitiveField(IDType.USER_ID)`,日志里的 `user.id` 还是明文 `12345`,没有包装成 `[IDTYPE:user_id:12345]`。把 `@ToString` 去掉后现象消失。

**根因**:SLF4J `{}` 占位符替换调用的是 `user.toString()`。SDK IDTYPE 包装的闭环要求 `toString()` 委托给 `SensitiveAwareObjectMapper.LOG_INSTANCE`(见 [sdk-usage.md §3](sdk-usage.md#3-idtype--sensitivefield))。Lombok `@ToString` 自动生成的 `toString()` 直接读各字段原始值,**完全绕过 Jackson 反射路径**,`@SensitiveField` 等同无效。

```java
// ❌ 错:Lombok @ToString 覆盖了正确的 toString()
@ToString          // ← Lombok 生成: return "User(id=" + id + ", email=" + email + ")"
public class User {
    @SensitiveField(IDType.USER_ID)
    private String id;

    @SensitiveField(IDType.EMAIL)
    private String email;
}

log.info("user {}", user);
// → "user User(id=12345, email=alice@a4x.ai)"  ← PII 明文!
```

**修复**:移除类级 `@ToString`,手动 override `toString()` 委托给 `SensitiveAwareObjectMapper.LOG_INSTANCE`:

```java
// ✅ 正确:移除 @ToString,手写委托
import com.a4x.logger.core.jackson.SensitiveAwareObjectMapper;
import com.fasterxml.jackson.core.JsonProcessingException;

// @ToString  ← 删掉
public class User {
    @SensitiveField(IDType.USER_ID)
    private String id;

    @SensitiveField(IDType.EMAIL)
    private String email;

    private String name;   // 无注解,原样输出

    @JsonIgnore
    private String password;   // Jackson introspection 阶段已剔除,绝不出现

    @Override
    public String toString() {
        try {
            return SensitiveAwareObjectMapper.LOG_INSTANCE.writeValueAsString(this);
        } catch (JsonProcessingException e) {
            return "<serialization-error>";
        }
    }
}

log.info("user {}", user);
// → "user {\"id\":\"[IDTYPE:user_id:12345]\",\"email\":\"[IDTYPE:email:alice@a4x.ai]\",\"name\":\"alice\"}"
```

如果 POJO 因其他原因需要保留 `@ToString`(如调试用),可配合 `@ToString.Exclude` 排除敏感字段,但 SDK IDTYPE 路径仍然需要手写 `toString()` 委托——两者不可替代对方。

**自检 grep**:

```bash
# 找同时有 @ToString 和 @SensitiveField 的类(高危)
grep -rln '@ToString' --include='*.java' src/main/java/ | xargs grep -l '@SensitiveField' 2>/dev/null

# 找有 @SensitiveField 但没有 writeValueAsString 的类(可能漏了 toString 委托)
grep -rln '@SensitiveField' --include='*.java' src/main/java/ | xargs grep -L 'writeValueAsString' 2>/dev/null
```

---

### 6. Spring DevTools 重启时 SDK 配置缓存

**现象**:本机开发用 `spring-boot-devtools` 热重启后,日志 JSON 字段出现异常——`schema_version` 字段消失,`service` / `instance` 不出现,或 masking 规则不生效(已配规则不 mask)。完整重启 JVM 后现象消失。

**根因**:Spring DevTools 重启走的是**应用 ClassLoader 热替换**,不重启 JVM。DevTools 把应用的 Bean(含 `A4xLoggerAutoConfiguration` 注册的静态状态)销毁再重建,但 **SDK 部分静态字段**(`MaskingState` / `ServiceInstanceJsonProvider` 的 static 变量)挂在**平台 ClassLoader**下,不随 DevTools 重启刷新。新的 `A4xLoggerAutoConfiguration` bean 实例(新 AppClassLoader)再次调用静态 setter 时,如果 JVM 缓存了旧值(取决于 SDK 静态初始化时序),可能出现新旧配置混用:

- `MaskingState.set(...)` 在 DevTools 重启后被新 bean 重调,但中间有一段窗口期走旧 `MaskingState`
- Logback 的 `LoggerContext` 默认不随 DevTools 重置,`a4x-logger-base.xml` 里注册的 `conversionRule` 在第二次 `<include>` 时可能被跳过(Logback 检测到已注册)

**修复**:

```yaml
# application.yml — 本机开发环境(profile: local / dev)
spring:
  devtools:
    restart:
      enabled: false   # 关掉 DevTools 重启,用完整 IDE 重启代替
```

如果团队依赖 DevTools 热重启提速,把 SDK 相关的 `ClassLoader` 隔离配置加入 `spring-devtools.properties`:

```properties
# src/main/resources/META-INF/spring-devtools.properties
# 让 SDK 包走平台 ClassLoader,避免被 DevTools 重载搞乱静态状态
restart.exclude.a4x-logger=/com/a4x/logger/.*
```

**验证方法**:DevTools 重启后立即看 `logger_init` 事件——该事件由 `A4xLoggerInitEmitter` 在 bean 初始化时 emit,如果重启后有且仅有一条 `logger_init` 且字段完整(`sdk_version` / `schema_version` 都在),说明配置正常;如果缺字段或没有 `logger_init`,说明 AutoConfig 没有完整跑。完整重启 JVM 后对照。

**自检 grep**:

```bash
# 检查是否引入了 devtools 依赖
grep -n 'spring-boot-devtools' pom.xml

# 检查是否有 DevTools 的 restart.exclude 配置
find src/main/resources/META-INF -name 'spring-devtools.properties' 2>/dev/null
```

---

## 速查表

接入快 release 前过一遍:

- [ ] 所有 Filter 的 `MDC.clear()` 在 **`finally`** 里(坑 1)
- [ ] Filter 用 `FilterRegistrationBean` 显式注册,顺序可控(坑 2)
- [ ] `logback-spring.xml` 的 `AsyncAppender`(如有)显式配 `<discardingThreshold>0</discardingThreshold>`(坑 3)
- [ ] OTel Java Agent 和 OTel SDK logback bridge **二选一**,不双装(坑 4)
- [ ] 有 `@SensitiveField` 的 POJO 手写 `toString()` 委托给 `SensitiveAwareObjectMapper.LOG_INSTANCE`,无 Lombok `@ToString` 干扰(坑 5)
- [ ] 本机开发如用 DevTools,观察到异常时完整重启 JVM 再 verify;生产 / CI 不受影响(坑 6)
