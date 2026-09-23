# Execution — Java 接入流程纪律

> 本文件教 **agent 怎么按流程接入 Java 服务 + 怎么保证业务无回归**。Phase 0 Recon 通过后走本文件。
>
> 配套文件:
> - SDK API 用法 → [sdk-usage.md](sdk-usage.md)
> - 项目探查 → [../project-recon.md](../project-recon.md)
> - 新写日志规约 → [../log-style-guide.md](../log-style-guide.md)(soft,migration 不强制)
> - 通用坑 → [common-pitfalls.md](common-pitfalls.md)
> - PII 扫描 → [pii-scanner.md](pii-scanner.md)
>
> **基准版本**:`a4x-logger-starter` 1.0.0 / JDK 11 / Spring Boot 2.5.5 ~ 2.7.x

---

## §1 7 步骨架

Spring Boot starter 把 SDK 初始化自动化,**不需要 Go 的 11 步全量**。按下表执行,
**每行右侧适用性看 §2 表,机械跑全 7 步可能做无用功**。

| # | 步骤 | 变动量 | 可独立上线 | 备注 |
|---|---|---|---|---|
| 0 | **Pre-flight baseline** — `mvn -DskipITs=false verify` 记 failing tests + 生成 smoke curl 脚本 | 1 个脚本 | n/a | [详](#step-0-baseline) |
| 1 | **加 starter dep + application.yml**(含完整 mask rules) | `pom.xml` + `application.yml` | ✅ | [详](#step-1-dep--yml) |
| 2 | **MDC 写入方决策**(自家 Filter / LoggingContextFilter 二选一,加 `finally { MDC.clear() }`) | 1-2 Filter 文件 | ✅ | [详](#step-2-mdc-写入方决策) |
| 3 | **Request/Response POJO 加 `@SensitiveField` 注解 + `toString()` 委托** | M 个 POJO | ✅ 静态改 | [详](#step-3-pojo-注解) |
| 4 | **现有 SLF4J 日志 PII 写法纠错**(字符串拼接 → `{}` 占位符) | 业务代码,可分批 | ✅ | [详](#step-4-pii-纠错) |
| 5 | **测试改写** — 旧 `Logger.setOutput(buf)` 捕获 → `LogCapture` try-with-resources | test 文件 | ✅ | [详](#step-5-测试改写) |
| 6 | **CI/Docker 私服鉴权**(Maven `settings.xml` + `CI_JOB_TOKEN`) | `.gitlab-ci.yml` + `settings.xml` | ✅ | [详](#step-6-cidocker) |
| 7 | **写设计文档 / 交付总结** | 1 份 md | ✅ | [详](#step-7-文档) |

---

## §2 按模式查 Step 适用性

Recon 探到的"项目现状"决定每步的实际工作量。**Agent 不要机械跑全 7 步**。

| Step | 全新接入 | 已有日志迁移 |
|---|---|---|
| 0 baseline | ⚠️ 只跑 `mvn verify` 记失败数;**跳过 smoke discover**(项目没业务 endpoint) | ✅ 完整(test + smoke curl) |
| 1 dep+yml | ✅ | ✅ |
| 2 MDC | ✅(项目无 Filter → 新建 `LoggingContextFilter`,`user_id` 块留 TODO 注释待 auth 接入) | ⚠️ 若已有 `AuthFilter` / `JwtFilter` 写入,在自家改 + 加 `finally MDC.clear()`,不再挂 SDK LoggingContextFilter(避免双写) |
| 3 POJO 注解 | ✅(新 POJO 直接打注解) | ✅ |
| 4 PII 纠错 | ⚠️ 全新接入只在 demo/example 代码里纠错 | ✅ **主战场** |
| 5 测试改写 | ⚠️ 全新接入可后做 | ✅ |
| 6 CI/Docker | ✅ | ✅ |
| 7 文档 | ⚠️ 全新接入写 setup doc(短) | ✅ 写 migration doc |

**符号**:✅ 必做 / ⚠️ 视情况调整 / ➖ 跳过。

---

## §3 Step 1 详细 — dep + yml

### Step 0 baseline {#step-0-baseline}

**目的**:建立回归基准,接入后能 diff。

```bash
# 1. 记现有测试失败数(baseline)
mvn -DskipITs=false verify 2>&1 | tee /tmp/baseline-test.log
grep -E 'Tests run:|BUILD' /tmp/baseline-test.log

# 2. 已有服务:生成 smoke curl 脚本(见 ../regression-smoke-template.md)
# 全新接入:跳过 smoke discover,没有业务 endpoint 可 smoke
```

记录失败数量到 `/tmp/baseline-test.log`。后续每步的 7 项验证都要对比这个数字。

### Step 1 详细:加 starter dep + application.yml {#step-1-dep--yml}

#### 1a. `pom.xml` 加依赖

```xml
<dependency>
    <groupId>com.a4x.logger</groupId>
    <artifactId>a4x-logger-starter</artifactId>
    <version>1.0.0</version>  <!-- 权威版本见 version-matrix.md -->
</dependency>
```

> **版本权威**:具体推荐版本以 SDK 仓 `docs/architecture/version-matrix.md §2.2` 为准;
> 接入步骤不随版本变化。

非 Spring Boot 项目:引 `a4x-logger-format` + `a4x-logger-core` + `logback-classic`,
见 [sdk-usage.md §1](sdk-usage.md#1-5-分钟接入) 的注释。

#### 1b. `application.yml` — 完整 mask rules 模板(phone 6 条 + email 4 条 + api-token 2 条)

以下是**推荐起步模板**(来源:SDK `docs/architecture/java/usage.md §6.2`)。
**按项目实际 PII 字段增删规则**,不要原样全部保留。

```yaml
a4x-logger:
  service-name: ${SERVICE_NAME:my-service}   # 优先读 env,无 env 时显式配
  instance: ${HOSTNAME}                       # 不要写 ${HOSTNAME:unknown}!:- 让 null-omit 失效

  level: INFO
  max-message-length: 32768                  # 默认 32768(32KB),最小 256

  sensitive:
    enabled: true
    on-error: log_raw                        # masking 失败时 fallback:log_raw / redact / drop
    masks:
      # label-driven:rule key 出现在 message 中才触发,覆盖 log 中 phone/email/api-token 各种文本形态
      phone:
        - 'phone:\s*([^,\s"$]+)'             # phone:VALUE 或 phone: VALUE
        - 'phone=([^,\s"$]+)'               # phone=VALUE
        - 'phone\s+([^,\s"$]+)'             # phone VALUE(空格分隔)
        - 'phone":"([^,\s"$]+)'             # JSON: "phone":"VALUE"
        - 'phone":\s+"([^,\s"$]+)'          # JSON: "phone": "VALUE"(带空格)
        - 'phone\\":\\"([^,\s"$\\$]+)'      # 转义 JSON: \"phone\":\"VALUE\"
      email:
        - 'email:\s*([^,\s"$]+)'
        - 'email=([^,\s"$]+)'
        - 'email":"([^,\s"$]+)'
        - 'email":\s+"([^,\s"$]+)'
      api-token:
        - 'api-token:\s*([^,\s"$]+)'
        - '"api-token":"([^,\s"$]+)'

  # 旧项目迁移期:canonical key 不在 MDC 时走 alias 回退
  context:
    aliases:
      user_id:
        - userId
        - uid
      firmware_version:
        - firmwareVersion
        - firmwareId
```

> **mask rule 扩展原则**:新增 PII 字段时,对照 [../fields-and-idtype.md](../fields-and-idtype.md)
> IDTYPE 变体表,逐字段写 label-driven 规则。值 shape 写法(如 `\b1[3-9]\d{9}\b`)漏匹配风险高,
> 不推荐。

#### 1c. Tier 2 项目:写 `logback-spring.xml`

如果项目有 Sentry / OTel / File Appender 等额外 Appender,需要 `logback-spring.xml`;
Tier 1 项目不写(SDK 全量接管)。详见 [sdk-usage.md §1](sdk-usage.md#1-5-分钟接入)。

---

## §4 Step 2 详细 — MDC 写入方决策 {#step-2-mdc-写入方决策}

### 决策树

```
Q: 项目已有 AuthFilter / JwtFilter 在请求入口提取 user_id 等 canonical 字段?
├── 是 → 在自家 Filter 里 MDC.put,不再挂 LoggingContextFilter(避免双写覆盖)
│         只需:
│         1. 确认 MDC.put key 是 canonical 名(见下表),或在 aliases 配了映射
│         2. 把 MDC.clear() 移到 finally 块(线程复用安全)
└── 否 → 业务方自行实现 LoggingContextFilter(从 header 提取字段写入 MDC;
          模板见 sdk-usage.md §2 写法 B,直接拷贝即可)
          全新接入:user_id 块先留 TODO,等 auth 接入后解开
```

**canonical MDC key 速查**(12 个,详见 [sdk-usage.md §2](sdk-usage.md#2-mdc-写入方约定)):

| 分类 | key |
|---|---|
| Tracing | `trace_id` / `span_id`(OTel 自动,勿手动) / `request_id` |
| Context | `user_id` / `tenant_id` / `account_id` / `serial_number` / `model_no` / `firmware_version` / `device_msg_src` / `session_id` / `task_name` |

### Tomcat 线程复用事故场景

> **已发生过的真实事故**:Tomcat 线程 #5 处理请求 A,Filter 跑完后 `MDC.put("user_id","alice")`;
> Controller 内部抛异常被全局异常处理器吞掉但**没 clear MDC**;Tomcat 立即把 #5 复用给请求 B,
> B 的 Filter 因某条件分支没 put `user_id`—— B 内部 `log.info` 拿到的依然是 `user_id=alice`。
> **教训:`MDC.clear()` 必须放 `finally`,不是 `try` 末尾。**

```java
// ✅ 正确:finally 保证任何退出路径都 clear
try {
    // ... MDC.put + 业务逻辑
    chain.doFilter(request, response);
} finally {
    MDC.clear();   // 不可省
}
```

### 同字段单写入方红线

同一个 canonical 字段**只能有一个写入方**。若自家 AuthFilter 已写 `user_id`,
不要再让 LoggingContextFilter 也写——会产生双写覆盖,难以排查谁覆盖了谁。

### 写法参考

三种具体写法的完整代码见 [sdk-usage.md §2](sdk-usage.md#2-mdc-写入方约定):
- **写法 A**:项目已有 AuthFilter / JwtFilter(最常见)
- **写法 B**:网关 / Mesh 注入 header,服务内做 header → MDC 映射
- **写法 C**:定时任务 / 批处理(无 HTTP 上下文)

---

## §5 Step 3-4 详细 — POJO 注解 + PII 纠错 {#step-3-pojo-注解}

### Step 3:POJO `@SensitiveField` 注解 + `toString()` 委托

#### 3a. 发现命令

```bash
# 找 PII 字段名的 POJO(对照 fields-and-idtype.md 变体表)
grep -rn --include="*.java" \
  -E '\b(userId|userID|email|phone|mac|macAddr|serialNo|serialNumber|ticketId|userSn|deviceSn|deviceMac)\b' \
  <项目-domain-path>
```

对照 [../fields-and-idtype.md](../fields-and-idtype.md) IDTYPE 变体表,逐字段加
`@SensitiveField(IDType.XXX)` 注解(具体 IDType 枚举值见 [sdk-usage.md §3](sdk-usage.md#3-idtype--sensitivefield))。

#### 3b. `toString()` override — 必做,不做等于注解无效

SLF4J `{}` 占位符调的是 `toString()`,不是独立 serialize 给 Jackson。
**不 override 则 `@SensitiveField` 等同无效**。

```java
import com.a4x.logger.core.jackson.SensitiveAwareObjectMapper;
import com.fasterxml.jackson.core.JsonProcessingException;

public class UserRequest {
    @SensitiveField(IDType.USER_ID)
    private String userId;

    @SensitiveField(IDType.EMAIL)
    private String email;

    private String name;   // 无注解,原样输出

    /**
     * 必须 override toString() 委托给 LOG_INSTANCE。
     * Lombok @ToString 会生成裸值 toString,绕开 LOG_INSTANCE —— 用了 Lombok 的 POJO
     * 需要在类级别 exclude 全部字段后手写此方法。
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

> **Lombok 陷阱**:`@ToString` 生成的 `toString()` 走裸字段值,会绕开 `LOG_INSTANCE`。
> 含 PII 字段的 POJO 如果用了 `@ToString`,在类上加 `@ToString(exclude = {...})` 或直接
> 删 `@ToString`,手写委托给 `LOG_INSTANCE`。详见 [common-pitfalls.md](common-pitfalls.md)。

### Step 4:PII 写法纠错(机械替换 4 规则) {#step-4-pii-纠错}

**核心立场**:migration 不是重构。目标是把 PII 送进 SDK 管线,**不是顺手规约化所有历史日志**。

#### 4 条规则

| 规则 | 含义 |
|---|---|
| **① API 必换** | `System.out.println` / `e.printStackTrace()` → SLF4J `log.info` / `log.error(..., e)` |
| **② message 文本基本保留** | **不发明 action 名,不强加 `result=`,不重命名变量**。原 message 啥样,换完基本啥样 |
| **③ inline 动态值可套 `{}`**(可选) | `log.info("user " + id)` → `log.info("user {}", id)`,结构化友好但不强制 |
| **④ SDK 硬要求** | (a) exception 当**最后**参数:`log.error("msg", e)`,SLF4J 特殊处理 stack trace<br>(b) `try { ... } catch (Exception e) { log.error("...", e); throw; }` — 不要 swallow exception |

#### 发现命令

```bash
# 扫字符串拼接打 PII 的写法
grep -rn --include="*.java" \
  -E 'log\.\w+\(".*"\s*\+|log\.\w+\(String\.format|log\.\w+\(.*\.format\(' \
  src/main/java/

# 扫裸 System.out / printStackTrace
grep -rn --include="*.java" \
  -E 'System\.out\.|\.printStackTrace\(\)' \
  src/main/java/
```

#### 典型替换示例

```java
// ❌ 拼接 → IDTYPE 失效,PII 明文落盘
log.info("user " + user.toString());
log.info(String.format("user %s", user));

// ✅ {} 占位符,SDK 拿到原始对象,@SensitiveField 反射生效
log.info("user {}", user);
```

```java
// ❌ 吞 exception
try { ... } catch (Exception e) { log.error("failed"); }

// ✅ exception 当最后参数,SLF4J 自动抽 stack trace 到 exception 字段
try { ... } catch (Exception e) { log.error("failed: {}", e.getMessage(), e); throw e; }
```

```java
// ❌ System.out / printStackTrace
System.out.println("starting");
e.printStackTrace();

// ✅ SLF4J
log.info("starting");
log.error("unexpected error", e);
```

#### 不该做的

- ❌ 不要给每条 `log.info("user " + id)` 强行起 `action=user_found` — 命名决策等于重写业务
- ❌ 不要把 `uid` 变量重命名成 `user_id` — 那是变量重构,不是日志迁移
- ❌ migration 时不套 [../log-style-guide.md](../log-style-guide.md) 的 `action=/result=` convention — 那是**新写日志推荐**

#### 分批策略

| 规模 | 建议 |
|---|---|
| < 20 个调用点 | 1-2 个 commit 搞定 |
| 20-100 个调用点 | 5-10 批,按 domain / package 分 |
| > 100 个调用点 | 按 domain 分;单 domain > 30 个调用点再按子包细分 |

---

## §6 Step 5-7 详细

### Step 5:测试改写 {#step-5-测试改写}

#### 5a. 发现旧的捕获写法

```bash
# 找靠 Appender 直接替换 / ListAppender 等旧捕获的测试
grep -rln --include="*.java" \
  -E 'Logger\.getLogger|ch\.qos\.logback.*ListAppender|setAdditive|LoggerContext' \
  src/test/java/
```

#### 5b. 替换为 `LogCapture` try-with-resources

`LogCapture` 通过 test-jar 依赖复用,API 和完整示例见
[sdk-usage.md §6](sdk-usage.md#6-测试捕获)。

```java
// ✅ 标准模式
@Test
void someMethod_emitsExpectedLog() {
    try (LogCapture cap = LogCapture.start()) {
        MDC.put("user_id", "u-100");
        try {
            // 触发被测业务逻辑
            service.process(request);
        } finally {
            MDC.clear();
        }

        JsonNode line = cap.lastJsonLine();
        assertThat(line.get("user_id").asText()).isEqualTo("u-100");
        assertThat(line.get("message").asText()).contains("[IDTYPE:user_id:");
    }
}
```

**`logback-test.xml` 前提**:测试资源里必须有 `<include resource="a4x-logger-base.xml"/>`,
否则 `LogCapture` 初始化会抛 `IllegalStateException`。

完整断言 API 见 [sdk-usage.md §6](sdk-usage.md#6-测试捕获)。

### Step 6:CI/Docker {#step-6-cidocker}

详细模板见 [`ci-docker-template.md`](ci-docker-template.md)。骨架步骤:

1. **项目根加 `ci/settings.xml`**:包含 Nexus 私服地址 + `${CI_JOB_TOKEN}` 鉴权配置
2. **`.gitlab-ci.yml` job 加 `-s ci/settings.xml`**:
   ```yaml
   script:
     - mvn -s ci/settings.xml verify
   ```
3. **Dockerfile 多 stage**:
   ```dockerfile
   # builder stage:拉私服只在 build 阶段
   FROM maven:3.8-openjdk-11 AS builder
   COPY ci/settings.xml /root/.m2/settings.xml
   RUN mvn -s /root/.m2/settings.xml package -DskipTests

   # runtime stage:不带 settings.xml
   FROM openjdk:11-jre-slim
   COPY --from=builder /app/target/*.jar app.jar
   ```

> settings.xml **只在 builder stage 存在**,runtime image 不含任何 Nexus 凭据。

### Step 7:文档 {#step-7-文档}

#### 全新接入 → Setup Doc

路径:`docs/architecture/logging/a4x-logger-setup.md`

```markdown
# A4X Logger SDK 接入设计 — <service-name>

| 文档状态 | YYYY-MM-DD |
| 对应 MR | !<number> |
| SDK 版本 | 1.0.0 |

## 1. 接入目标
- 全新服务 day 1 用统一日志 SDK
- 5 点能力:结构化 JSON / PII mask / canonical 字段 / IDTYPE 反射 / trace 关联

## 2. 实装范围
  2.1 Maven dep + application.yml
  2.2 MDC 写入方:<LoggingContextFilter 路径>
  2.3 POJO 注解:<X 个 POJO>

## 3. 字段契约
  3.1 Canonical 字段:request_id(✅ 已注入);user_id(⚠️ 待 auth 接入后补)
  3.2 IDTYPE tag:仅示例 POJO 已示范
  3.3 PII mask:预置 phone / email 规则

## 4. 待办
  4.1 接入 auth 后:Filter 里 user_id 注入解开 TODO
  4.2 业务 endpoint 稳定后:补 smoke 回归脚本

## 5. 可追溯链接
  - MR / SDK repo / skill 链接
```

#### 已有迁移 → Migration Doc

路径:`docs/architecture/logging/a4x-logger-migration.md`

```markdown
# A4X Logger SDK 迁移设计 — <service-name>

| 文档状态 | YYYY-MM-DD |
| 对应 MR | !<number> |
| SDK 版本 | 1.0.0 |
| 作用范围 | `<directory>/` |

## 1. 背景与目标
  1.1 现状:日志实现(<原库>) + ~N 个调用点 + 当前 PII 风险
  1.2 目标:5 点能力(结构化 JSON / PII mask / canonical 字段 / IDTYPE 反射 / trace 关联)
  1.3 Non-goals:明确不做什么(避免 scope creep)

## 2. 迁移范围
  2.1 代码改动(文件级)
  2.2 配置改动(application.yml / CI / Dockerfile)
  2.3 测试改动

## 3. 字段契约
  3.1 Canonical 字段(MDC 写入方注入)
  3.2 IDTYPE 反射标签
  3.3 PII mask(application.yml)

## 4. 回滚策略
  4.1 完全回滚(git revert MR)
  4.2 降级:关闭 `a4x-logger.sensitive.enabled`
  4.3 快速关闭:移除 starter dep 恢复原日志库

## 5. 可追溯链接
  - MR / SDK repo / skill 链接
```

在 `docs/architecture/overview.md` 或 README 加一行 link 指向本文档(AI reviewer "可追溯链接" 检查要用)。

---

## §7 每批 7 项验证 + 合并前 5 项 gate

### 每批 7 项强制验证

**Agent 自己跑,不 delegate**。改完一批 → 跑 7 项全过 → commit → 下一批。
任何一项 fail,**STOP 修或 revert,不累积**。

| # | 动作 | 命令 |
|---|---|---|
| 1 | **API 清零** — 本批 `System.out` / `printStackTrace` 全替换 | `grep -rE 'System\.out\.|\.printStackTrace\(' src/main/java/<batch-dir>` 应 **0 命中** |
| 2 | **build 过** | `mvn -pl <module> verify -DskipITs=false` |
| 3 | **测试回归**:失败数 ≤ Step 0 baseline | 数 fail → 对比 `/tmp/baseline-test.log` |
| 4 | **PII 扫描清零** | 跑 [pii-scanner.md](pii-scanner.md),本批扫描 0 命中才过 |
| 5 | **smoke 回归**:本批 domain endpoint diff baseline 无差异 | `./scripts/smoke-curl.sh --domain <batch> > /tmp/after.json` → diff baseline |
| 6 | **commit message 含 step 标号** | 例:`feat(<domain>): migrate logs to a4x-logger (step 4.1/3)`;**别用 `chore:` 偷懒** |
| 7 | **调用方同步** — 改了 POJO 字段 → 所有 caller 都跟着改,build 过 | `grep -rn 'new <Class>(' --include=*.java` 确认编译过 |

**如果 #1 失败**:本批还有漏改的 → grep 找出来全换完。
**如果 #2 失败**:构造函数签名改动碰到测试 fixture → 修 fixture / 补默认值。
**如果 #3 / #5 失败**:不要硬推下一批。常见原因:手滑改了非日志代码 / 误改了业务逻辑。

### 合并前 5 项 gate

**5 项全过才发 MR**(已有日志迁移):

| # | gate |
|---|---|
| ① | smoke diff baseline 零差异 |
| ② | 测试失败数 ≤ Step 0 baseline |
| ③ | **全仓非 SDK 日志 API 清零**:`grep -rE 'System\.out\.|\.printStackTrace\(' src/main/` 排除 test + 注释,**0 命中** |
| ④ | **canonical 字段无双写入**:同一个 canonical key 只有一个写入方(grep Filter 文件确认) |
| ⑤ | **`@SensitiveField` 反射闭环验证**:用 `LogCapture` 抽样查 IDTYPE 渲染 — `assertThat(line.get("message").asText()).contains("[IDTYPE:")` 至少命中 1 次 |

**全新接入 gate**(简化 — 无 smoke baseline):
- 测试失败数 ≤ Step 0 baseline
- demo 新增 test 全绿(`LogCapture` 能验证日志格式生成)
- 可选:起服务 `curl` 一下 endpoint 肉眼对照 [../log-style-guide.md](../log-style-guide.md) pattern

---

## §8 MR 描述模板 / 交付总结模板

### MR 描述模板(已有日志迁移用)

```markdown
## Summary
- 迁移 <service> 的 <N> 处日志调用到 a4x-logger-starter 1.0.0
- 加 MDC 写入方(<Filter 名>)注入 <fields> canonical 字段
- POJO `@SensitiveField` 覆盖 <M> 个 PII 字段 + toString() 委托
- CI/Docker 配 Maven Nexus 私服鉴权

## Baseline 数字(mvn/grep 实测)
- Pre-integration test failures: <N>
- System.out / printStackTrace 调用总数: <count>
- 分批: <B> 批 commit(step 4.1 / 4.2 / ...)

## Final 数字
- Post-integration test failures: <N>(未劣化)
- 全仓 `System.out\.|\.printStackTrace` 命中: 0
- PII 拼接写法命中: 0
- Smoke diff baseline: 0 diff
- IDTYPE 反射闭环: `[IDTYPE:` 命中 ✓

## 合并前 5 项 gate
- ✅ ① smoke diff = 0
- ✅ ② test ≤ baseline
- ✅ ③ 全仓非 SDK 日志 API 清零
- ✅ ④ canonical 字段无双写入
- ✅ ⑤ @SensitiveField 反射闭环

## 相关
- 迁移设计文档: docs/architecture/logging/a4x-logger-migration.md
- SDK version: a4x-logger-starter 1.0.0
```

### 典型 commit 序列

```
1. test(smoke): add regression smoke baseline              (Step 0)
2. feat(config): add a4x-logger-starter dep + application.yml   (Step 1)
3. feat(filter): add LoggingContextFilter injecting canonical fields  (Step 2)
4. feat(types): add @SensitiveField to PII POJOs + toString override  (Step 3)
5. feat(<domain>): migrate PII log calls to {} placeholder (step 4.1/3)  (Step 4 batch 1)
6. feat(<domain>): migrate PII log calls (step 4.2/3)    (Step 4 batch 2)
7. test: replace log capture with LogCapture try-with-resources  (Step 5)
8. ci(docker): add Maven Nexus auth for private artifact  (Step 6)
9. docs(architecture): add a4x-logger migration design    (Step 7)
```

每个 commit 独立 review 友好 + 可独立 revert。

### 交付总结模板(agent 通过 text 输出给用户)

Step 7 结束时 agent 在对话里给用户这段 structured summary。占位符替换成真实值,
**数字 mvn/grep 实测,不编**。

```markdown
## a4x-logger 接入完成 — <service-name>

### 我做了

| 能力 | 产出物 |
|---|---|
| SDK 依赖 | `pom.xml`:a4x-logger-starter 1.0.0 |
| 配置文件 | `src/main/resources/application.yml`(mask rules 已预置 phone/email) |
| MDC 写入方 | `<filter-path>/<FilterName>.java`(request_id / user_id / ... 注入) |
| POJO 注解 | <M> 个 POJO 加了 @SensitiveField + toString 委托 |
| PII 纠错 | <N> 处字符串拼接 → {} 占位符 |
| 测试改写 | <K> 个测试换用 LogCapture |
| CI/Docker | `ci/settings.xml` + `.gitlab-ci.yml` + `Dockerfile` multi-stage |
| 设计文档 | `docs/architecture/logging/a4x-logger-<setup/migration>.md` |

### 未做项 / TODO(视项目情况)

| 项 | 为什么没做 | 后续怎么补 |
|---|---|---|
| CI 鉴权配置 | 项目当前无 `.gitlab-ci.yml` | CI 立项时按 [ci-docker-template.md](ci-docker-template.md) 加 |
| `user_id` 字段注入 | 项目当前无 auth(JWT/session) | 接入 auth 后解开 Filter 里的 TODO 注释 |
| Smoke 回归脚本 | 项目暂无业务 endpoint | 业务接口稳定后按 [../regression-smoke-template.md](../regression-smoke-template.md) 补 |

### Baseline 数字(实测)

| 指标 | 值 |
|---|---|
| Test failures (pre-integration) | <N> |
| Test failures (post-integration) | <N>(未劣化) |
| POJO 注解 | <M> 个 PII 字段 |
| PII 写法纠错 | <N> 处 |

### MR
<MR link>
```
