# Case Study — java-spring-demo

> **本 case-study 基于 SDK monorepo `examples/java-spring-demo`**,demo-driven baseline。
> 真实业务的 quirks(大型多 module 项目 / Spring Cloud Gateway / 复杂 Filter 链 / SAML/OAuth2)
> 等首个 Java 真接入服务补 production case-study。
>
> **类比 Go 的 naturehood.md** — 那是 production case-study(真实业务,21 commits,50 Logic struct)。
> 本文件是 baseline(demo,1 module,直接体现 SDK 默认接入路径,零存量迁移)。

| 维度 | 值 |
|---|---|
| 类型 | demo-driven baseline |
| 项目 | SDK monorepo `examples/java-spring-demo` |
| 规模 | 1 module,3 个 Java 文件(SpringDemo / UserController / LoggingContextFilter) |
| 接入路径 | Spring Boot starter 自动装配(Tier 2:自定义 logback-spring.xml) |
| SDK 版本 | `a4x-logger-starter` 1.0.0(pom.xml 实测) |
| Spring Boot | 2.5.5 / JDK 11 |
| 验证场景 | 9 个 curl 场景(8 核心 + 1 DFC 多字段) |
| 对照 demo | `java-pure-demo`(无 starter,手动初始化,两者对照阅读) |

---

## §1 项目规模与依赖树

### 1.1 Maven 依赖(pom.xml 全量)

demo 的 `pom.xml` 极简 — **业务方只需要两行 dep**:

```xml
<!-- 唯一一行业务方需要的 SDK 依赖 -->
<dependency>
    <groupId>com.a4x.logger</groupId>
    <artifactId>a4x-logger-starter</artifactId>
    <version>1.0.0</version>
</dependency>

<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-web</artifactId>
    <!-- 版本由 spring-boot-starter-parent 2.5.5 管理 -->
</dependency>
```

`a4x-logger-starter` 传递引入:
- `a4x-logger-core`(Jackson serializer / SensitiveField / IDType / MDC provider)
- `a4x-logger-format`(canonical JSON layout / Logback Appender)
- `logstash-logback-encoder`(StructuredArguments.kv / entries)
- `logback-classic`

### 1.2 源码文件清单

```
src/main/
├── java/com/a4x/logger/example/spring/
│   ├── SpringDemo.java           — @SpringBootApplication + Filter @Bean 注册
│   ├── LoggingContextFilter.java — HTTP header → MDC.put 写入方(业务方自行实现,非 SDK 提供;模板见 java/sdk-usage.md §2 写法 B)
│   └── UserController.java       — 9 个 endpoint,每个覆盖一个 SDK 场景
└── resources/
    ├── application.yml           — a4x-logger.* 配置(service-name / masks / aliases)
    └── logback-spring.xml        — Tier 2 一行 include
```

无 `src/test/`(demo 无单元测试,验证靠 curl 场景)。

### 1.3 关键配置片段

`application.yml`(完整内容):

```yaml
a4x-logger:
  service-name: java-spring-demo
  level: INFO
  max-message-length: 256
  sensitive:
    enabled: true
    on-error: log_raw
    masks:
      phone:
        - 'phone:\s*([^,\s"$]+)'
        - 'phone=([^,\s"$]+)'
        - 'phone\s+([^,\s"$]+)'
        - 'phone":"([^,\s"$]+)'
      email:
        - 'email:\s*([^,\s"$]+)'
        - 'email=([^,\s"$]+)'
  context:
    aliases:
      user_id: [userId]
      firmware_version: [firmwareId]

server:
  port: 8081   # 与 java-pure-demo (8080) 区分,可同时跑
```

`logback-spring.xml`(Tier 2,一行 include):

```xml
<configuration>
    <include resource="a4x-logger-base.xml"/>
    <root level="INFO"><appender-ref ref="CONSOLE"/></root>
</configuration>
```

---

## §2 接入轨迹(对照 java/execution.md 7 步)

> 完整 7 步规范见 [../java/execution.md](../java/execution.md)。
> demo 是 greenfield(非迁移),部分步骤 n/a。

| Step | 说明 | demo 实际 | 备注 |
|---|---|---|---|
| 0 baseline | `mvn verify` 记失败数 / smoke curl 脚本 | n/a | greenfield demo,无存量 endpoint,跳过 smoke discover |
| 1 dep + yml | pom.xml + application.yml(含 mask rules) | ✅ | `a4x-logger-starter` 1.0.0;phone 4 条 + email 2 条规则 |
| 2 MDC 写入方 | 自家 Filter vs LoggingContextFilter 二选一 | ✅ 自家 Filter | `LoggingContextFilter.java`:11 个 header → MDC + `finally MDC.clear()`;request_id UUID 兜底 |
| 3 POJO 注解 | `@SensitiveField` + toString() 委托 | ✅ | `UserController.User` 内部类:id(`USER_ID`) + email(`EMAIL`);无 toString() override(场景 5 直接调 `LOG_INSTANCE.writeValueAsString`) |
| 4 PII 纠错 | 字符串拼接 → `{}` 占位符 | n/a | greenfield,所有 log.info 写法从一开始就是 `{}` 占位符 |
| 5 测试改写 | ListAppender → `LogCapture` try-with-resources | n/a | demo 无 src/test/;验证靠 curl |
| 6 CI/Docker | Maven settings.xml + CI_JOB_TOKEN | n/a | demo 不独立打包;SDK monorepo CI 已覆盖 |
| 7 文档 | 接入设计 doc / migration doc | ✅ | `README.md` = demo baseline doc(curl 场景全列) |

**主动决策说明**:

- Step 3 的 `toString()` override — demo 的 `UserController.User` 没有覆写 `toString()`,场景 5 改用 `SensitiveAwareObjectMapper.LOG_INSTANCE.writeValueAsString(u)` 显式序列化后拼进 message。这是 demo 演示路径的刻意选择,不违背规范(规范允许两种形式,见 [../java/sdk-usage.md#3-idtype--sensitivefield](../java/sdk-usage.md));**真实业务 POJO 建议 override `toString()`**,避免每个调用点都要手工 serialize。
- Step 2 使用的是"自家 Filter"模式(写法 B),而不是 SDK 内置 `LoggingContextFilter`——这也是 demo 刻意示范业务方的标准做法。

---

## §3 验证场景表

服务跑在 `localhost:8081`。每个场景对应 `UserController.java` 里一个 endpoint。

| # | 场景 | curl 命令 | 期望输出要点 |
|---|---|---|---|
| 1 | 基本日志 + canonical null-omit | `curl localhost:8081/health` | JSON 无 `trace_id` / `user_id`(header 未传,null-omit 省略);有 `request_id`(UUID 兜底);`service="java-spring-demo"` |
| 2 | HTTP header → MDC → JSON | `curl -H 'X-Trace-Id:abc123' -H 'X-User-Id:user-99' localhost:8081/users/42` | `trace_id:"abc123"`、`user_id:"user-99"` 出现在 JSON 顶层 |
| 3 | exception 字段独立不脱敏 | `curl localhost:8081/error` | `message:"simulated error"`;`exception` 字段独立含完整 stack trace;**不**走脱敏管线(ADR-03) |
| 4 | phone masking | `curl localhost:8081/phone` | message `"user phone:13812345678"` → `"user ***"`;label-driven regex 由 application.yml 配置,starter 自动 wire |
| 5 | `@SensitiveField` + IDTYPE 包装 | `curl localhost:8081/sensitive` | message 含 `[IDTYPE:user_id:12345]` 与 `[IDTYPE:email:alice@a4x.ai]`;由 `LOG_INSTANCE` + `SensitiveFieldBeanSerializerModifier` 自动包装 |
| 6 | 动态字段 + DFC 冲突 | `curl localhost:8081/dynamic` | `order_id:"ORD-99887766"` 出现在 JSON 顶层;`trace_id` / `level` 是 SDK-managed 值(业务注入的假值被静默丢弃) |
| 7 | max-message-length 截断 | `curl localhost:8081/truncate` | message 末尾 `...[truncated]`;长度 ≤ 256(application.yml 配);IDTYPE-aware 不截断 `[IDTYPE:...]` 标记内部 |
| 8 | alias 映射 | `curl -H 'X-Firmware-Id:3.2.18' localhost:8081/alias` | JSON 出现 `firmware_version:"3.2.18"`,**不是** `firmwareId`;alias 在 application.yml 配,starter 自动接进 CanonicalContextJsonProvider |
| 9 | DFC 多字段 entries | `curl localhost:8081/dfc` | `order_id:"ORD-001"` 通过;`user_id:"spoofed-fake"` 被静默丢弃(reserved);`phone:13812345678` 被 phone rule 脱敏为 `***` |

**启动时验证**:服务启动会自动 emit 一条 `logger_init` 事件(ADR-10),含 `event=logger_init` + `sdk_version` + `schema_version` + `language=java`。

**场景 9 vs README**:README 列了 8 个场景,`UserController.java` 实际有 9 个 endpoint(多了 `/dfc`)。以源码为准。

---

## §4 demo 没覆盖的真实业务 quirks(待 production case-study 补)

待首个 Java 真接入服务的 owner 补充具体上下文:

- [ ] **大型 multi-module 项目的 application.yml 继承**:子模块 yml 覆盖父模块时,`a4x-logger.*` 配置优先级规则;starter AutoConfig 是否跟着子模块 classpath 走
- [ ] **Spring Cloud Gateway / WebFlux reactive 场景**:MDC 基于 ThreadLocal,reactor context 切换后 MDC 丢失;需要 `Hooks.onEachOperator` 或 `ContextView` 桥接方案
- [ ] **复杂 Filter 链顺序冲突**:SAML / OAuth2 / 自家 AuthFilter 运行在 `LoggingContextFilter` 之前,`user_id` 注入时机是否正确;双写入方排查(canonical 字段同字段单写入方红线,见 [../java/execution.md#2-mdc-写入方决策](../java/execution.md))
- [ ] **Logback 异步配置丢 `logger_init` 日志**:项目用 `<appender class="AsyncAppender">` + `<discardingThreshold>` 时,SDK 启动事件可能在队列满时被丢弃
- [ ] **mvn deploy 走 Nexus 私服**:业务方 CI 需要自建 `ci/settings.xml` + Job Token allowlist;SDK 的 `ci/java.yml` deploy job 目前未公开;具体踩坑路径见 [../java/ci-docker-template.md](../java/ci-docker-template.md)
- [ ] **Lombok 老版本 `@ToString` 行为差异**:Lombok 1.18.12 以前 `@ToString` 不识别 `@SensitiveField`,生成裸值 `toString()` 绕开 `LOG_INSTANCE`;升 Lombok 或手写 override,见 [../java/common-pitfalls.md](../java/common-pitfalls.md)

---

## 不适用 java-spring-demo 的情况

你的项目如果满足以下任何一条,**直接套 demo 的做法可能不够**:

- **非 Spring Boot** → starter AutoConfig 不生效,改用 `a4x-logger-format` + 手动初始化,参考 `java-pure-demo`
- **已有存量日志迁移** → demo 是 greenfield,无 Step 0 baseline / Step 4 PII 纠错 / Step 5 测试改写的参考;看 [../java/execution.md §2](../java/execution.md) 的"已有日志迁移"列
- **Spring Boot 3.x** → Filter API 从 `javax.servlet` 改为 `jakarta.servlet`;demo pom.xml 基于 Spring Boot 2.5.5,包名不同
- **Reactive / WebFlux** → MDC 方案完全不同,demo 的 `LoggingContextFilter` 不适用
- **POJO 数量多**:demo 只有 1 个内部类 `User`;真实业务 POJO 多时需系统性 grep + 批量注解,见 [../java/execution.md §5](../java/execution.md)
