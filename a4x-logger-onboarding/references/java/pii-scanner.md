# Java 业务代码 PII 扫描 + 自动改写

**目的**:迁移完 SDK 接入逻辑后,**主动扫**业务代码里裸 PII 的日志调用——字符串拼接 / `String.format` / Lombok `@ToString` / `System.out`。改成 SLF4J `{}` 占位符 + POJO `@SensitiveField` 路径,让 SDK IDTYPE 反射真正生效。

> 背景规则见 [sdk-usage.md §3](sdk-usage.md#3-idtype--sensitivefield)，Lombok 坑见 [common-pitfalls.md §5](common-pitfalls.md#5-lombok-tostring-把-pii-字段当裸值打)。

---

## §1 4 阶段 recipe

| 阶段 | 动作 | 产出 |
|---|---|---|
| **Discover** | 跑 §2–§5 的 grep,汇总所有命中行 | `pii-hits.txt` |
| **Classify** | 逐条判定:裸标量 / struct field / 上下文描述(不是打值) | 分类标注 |
| **Fix** | 裸标量 → SLF4J `{}` 占位符；POJO → 手写 `toString()` 委托 | 改写 diff |
| **Verify** | `mvn compile` 通过；`mvn test` 绿；`LogCapture` 断言 IDTYPE 包装出现 | 无新告警 |

---

## §2 字符串拼接打日志

**为什么危险**:SLF4J 拿到的已是 final string，SDK 没有任何反射切口，`@SensitiveField` 物理上无法生效（见 [sdk-usage.md §3](sdk-usage.md#3-idtype--sensitivefield)）。

```bash
cd <service-root>
grep -rnE 'log\.(info|warn|error|debug)\s*\(\s*"[^"]*"\s*\+' \
  --include='*.java' src/main/java/
```

**命中示例**:

```java
// ❌ 字符串拼接:PII 明文落盘
log.info("user " + userId);
log.info("device " + device.getSerialNumber() + " connected");
log.error("send mail failed, email=" + user.getEmail(), e);
```

> false positive：注意区分注释行和测试类（见 §7）。

---

## §3 String.format 打日志

```bash
grep -rnE 'log\.(info|warn|error|debug)\s*\(\s*String\.format\s*\(' \
  --include='*.java' src/main/java/
```

也要覆盖先 `String.format` 存变量再传的形式：

```bash
grep -rnE 'String\.format\s*\(.*\b(user_?[iI]d|userId|uid|email|device_?[sS]n|serialNumber|device_?[mM]ac|ticket_?[iI]d|user_?[sS]n)\b' \
  --include='*.java' src/main/java/
```

**命中示例**:

```java
// ❌ String.format：SDK 只看到已格式化字符串
log.info(String.format("user %s logged in", userId));
String msg = String.format("email=%s", user.getEmail());
log.warn(msg);
```

---

## §4 @ToString 含 PII 字段

Lombok `@ToString` 生成的 `toString()` 直接读字段原始值，完全绕过 Jackson 反射路径，`@SensitiveField` 等同无效（详见 [common-pitfalls.md §5](common-pitfalls.md#5-lombok-tostring-把-pii-字段当裸值打)）。

```bash
# 找同时有 @ToString 和 @SensitiveField 的类(高危)
grep -rln '@ToString' --include='*.java' src/main/java/ \
  | xargs grep -l '@SensitiveField' 2>/dev/null

# 找有 @SensitiveField 但缺 writeValueAsString 委托的类
grep -rln '@SensitiveField' --include='*.java' src/main/java/ \
  | xargs grep -L 'writeValueAsString' 2>/dev/null
```

**命中示例**:

```java
// ❌ Lombok @ToString：PII 原始值出现在日志
@ToString
public class User {
    @SensitiveField(IDType.USER_ID) private String id;
    @SensitiveField(IDType.EMAIL)   private String email;
}

log.info("user {}", user);
// → "user User(id=12345, email=alice@a4x.ai)"  ← PII 明文
```

---

## §5 System.out / printStackTrace 兜底

这些调用完全绕过 Logback 和 SDK，PII 直接打到 stdout / stderr，线上必须清零。

```bash
# System.out.print / println
grep -rn 'System\.out\.print' --include='*.java' src/main/java/

# e.printStackTrace() — 异常 stacktrace 含 PII 消息时同样危险
grep -rn '\.printStackTrace()' --include='*.java' src/main/java/
```

---

## §6 Auto-rewrite recipe

### 6.1 字符串拼接 → SLF4J `{}` 占位符

```java
// ❌ before
log.info("user " + userId + " login");
log.error("send mail failed email=" + user.getEmail(), e);
log.warn("device=" + device.getSerialNumber() + " offline");

// ✅ after（SDK 看到原始对象，IDTYPE 反射生效）
log.info("user {} login", userId);
log.error("send mail failed email={}", user.getEmail(), e);
log.warn("device={} offline", device.getSerialNumber());
```

> **注意**：`throwable` 参数放最后，SLF4J 自动识别并抽到 `exception` 字段，**不要**在 message 里占位。

### 6.2 String.format → `{}` 占位符

```java
// ❌ before
log.info(String.format("user %s logged in", userId));
String msg = String.format("ticket %s assigned", ticketId);
log.warn(msg);

// ✅ after
log.info("user {} logged in", userId);
log.warn("ticket {} assigned", ticketId);
```

### 6.3 Lombok @ToString → 手写 toString() 委托 LOG_INSTANCE

这是唯一能让 `@SensitiveField` 真正生效的写法（闭环原理见 [sdk-usage.md §3](sdk-usage.md#3-idtype--sensitivefield)）：

```java
// ❌ before：Lombok @ToString 绕开 Jackson
import lombok.ToString;

@ToString
public class User {
    @SensitiveField(IDType.USER_ID) private String id;
    @SensitiveField(IDType.EMAIL)   private String email;
    private String name;

    @JsonIgnore
    private String password;
}
```

```java
// ✅ after：移除 @ToString，手写委托 SensitiveAwareObjectMapper.LOG_INSTANCE
import com.a4x.logger.core.annotation.SensitiveField;
import com.a4x.logger.core.enums.IDType;
import com.a4x.logger.core.jackson.SensitiveAwareObjectMapper;
import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.core.JsonProcessingException;

// @ToString  ← 删掉
public class User {
    @SensitiveField(IDType.USER_ID) private String id;
    @SensitiveField(IDType.EMAIL)   private String email;
    private String name;

    @JsonIgnore
    private String password;   // Jackson introspection 阶段已剔除，绝不出现

    /**
     * 必须委托给 LOG_INSTANCE：SLF4J {} 调的是 toString()，
     * 不走这条路 @SensitiveField 等同无效。
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

// 改写后日志输出
log.info("user {}", user);
// → "user {\"id\":\"[IDTYPE:user_id:12345]\",\"email\":\"[IDTYPE:email:alice@a4x.ai]\",\"name\":\"alice\"}"
```

**如果 POJO 因调试等原因需要保留 `@ToString`**，用 `@ToString.Exclude` 排除全部 `@SensitiveField` 字段，然后仍然必须手写 `toString()` 委托——`@ToString.Exclude` 不能替代 `LOG_INSTANCE` 路径。

### 6.4 System.out → SLF4J

```java
// ❌ before
System.out.println("user " + userId + " connected");
e.printStackTrace();

// ✅ after
log.info("user {} connected", userId);
log.error("操作失败", e);   // throwable 放最后，SDK 抽到 exception 字段
```

---

## §7 误报抑制

下列情况正则可能命中但不是真正的 PII 泄漏，**先不改，人工确认**：

| 情形 | 原因 | 建议 |
|---|---|---|
| `src/test/java/` 下的测试类 | 测试日志不上生产，风险低 | grep 加 `--exclude-dir=test` 或 review 后决定 |
| `log.info("checking user_id mapping %s", tableName)` | `user_id` 是上下文描述词，不是打值 | 人工确认后跳过 |
| exception message 含 PII（`e.getMessage()` 里） | 根本原因在异常构造处 | 标记 TODO，由业务开发处理 |
| `// comment: user " + uid` 注释里的字符串 | 不执行 | 可忽略 |
| `"url=/api/users/" + pathVar` | URL 路径片段，`pathVar` 不一定是 PII | 人工判断 pathVar 含义 |

**操作规则**：疑似但语义不确定 → **不改，加 `// TODO(pii-review):` 注释**，让业务开发 review。false positive 自动改反而搞乱语义。

---

> **改写完必须跑**：`mvn compile && mvn test -pl <module>` 绿灯后单独提交：`"chore: wrap PII scalars with SLF4J placeholders and fix toString() delegation"`。不要与 MDC / SDK 接入逻辑的 commit 混在一起。
