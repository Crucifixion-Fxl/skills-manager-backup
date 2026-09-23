# Python 业务代码 PII 扫描 + 自动改写

**目的**:接入 SDK 后,**主动扫**业务代码里裸 PII 的日志调用——f-string / `.format()` / `+` 拼接 / 裸 `print()`。改成 `%s` 占位符 + dataclass `metadata={"sensitive": ...}` 路径,让 SDK IDTYPE 反射真正生效。

> 背景规则见 [sdk-usage.md §3](sdk-usage.md#3-idtype--dataclass-metadata),f-string 坑见 [common-pitfalls.md §5](common-pitfalls.md#5-f-string--format--拼接-pii-字段绕过-idtype-反射),print 坑见 [common-pitfalls.md §6](common-pitfalls.md#6-裸-print-调用绕过整个日志管线)。

---

## §1 4 阶段 recipe

| 阶段 | 动作 | 产出 |
|---|---|---|
| **Discover** | 跑 §2–§5 的 grep,汇总所有命中行 | `pii-hits.txt` |
| **Classify** | 逐条判定:裸 PII 值 / 上下文描述词(不是打值) / 误报 | 分类标注 |
| **Fix** | 裸 PII 值 → `%s` 占位符传原始对象；dataclass → 加 `metadata={"sensitive": ...}` | 改写 diff |
| **Verify** | `ruff check` + `pytest` 绿；`log_capture` 断言 `[IDTYPE:...]` 包装出现 | 无新告警 |

---

## §2 f-string 打日志

**为什么危险**:f-string 在进入 structlog processor 链**之前**就把对象变成字符串,SDK 拿到的已是 final string,`dataclasses.fields()` 反射路径完全静默跳过,PII 明文落盘(见 [sdk-usage.md §3](sdk-usage.md#3-idtype--dataclass-metadata))。

```bash
cd <service-root>
grep -rnE 'log\w*\.\w+\(f"' --include='*.py' src/
```

**命中示例**:

```python
# ❌ f-string:IDTYPE 反射失效,PII 明文落盘
logger.info(f"user {user_id} login")
logger.warning(f"device {device.serial_number} offline")
logger.error(f"send mail failed, email={user.email}", exc_info=True)
```

> false positive:注意区分注释行和测试类(见 §7)。

---

## §3 `.format()` 打日志

**为什么危险**:同 §2——`.format()` 在 SDK 入口前完成字符串化,IDTYPE 反射静默失效。

```bash
grep -rnE 'log\w*\.\w+\(.*\.format\(' --include='*.py' src/
```

也要覆盖先 `.format()` 存变量再传的形式:

```bash
grep -rnE '"[^"]*\{[^}]*\}"\.format\(' --include='*.py' src/ \
  | grep -iE 'user_?id|uid|email|serial|device|ticket|account'
```

**命中示例**:

```python
# ❌ .format():SDK 只看到已格式化字符串
logger.info("user {} login".format(user_id))
msg = "email={}".format(user.email)
logger.warning(msg)
```

---

## §4 `+` 拼接打日志

**为什么危险**:同 §2。拼接后 SDK 看到完整字符串,反射入口不存在。

```bash
grep -rnE 'log\w*\.\w+\(".*"[[:space:]]*\+' --include='*.py' src/
```

补充扫先拼后传的变量:

```bash
grep -rnE \
  '(user_?id|uid|email|serial_?number|device_?sn|ticket_?id|account_?id)[^=]*=[^=].*\+' \
  --include='*.py' src/ | grep -v test
```

**命中示例**:

```python
# ❌ + 拼接:反射路径不存在,PII 明文落盘
logger.info("user " + user_id + " login")
logger.error("send mail failed email=" + user.email)
logger.warning("device=" + str(device.serial_number) + " offline")
```

---

## §5 裸 `print()` 调用

**为什么危险**:`print()` 直接写 stdout,完全绕过 structlog / SDK pipeline:无 canonical 字段(`trace_id` / `user_id` 等)、无 IDTYPE 脱敏、无 OTel 关联、无 `timestamp` / `level` / `service`。ELK Logstash JSON filter 拿到 plain-text 行后 parse 失败(`_grokparsefailure`),日志被丢弃(详见 [common-pitfalls.md §6](common-pitfalls.md#6-裸-print-调用绕过整个日志管线))。

```bash
# 找非注释的 print( 调用(排除 test 和 noqa 行)
grep -rn '^\s*print(' --include='*.py' src/ | grep -v test | grep -v '# noqa'

# 统计数量:0 为目标
grep -rc '^\s*print(' --include='*.py' src/ | grep -v ':0' | grep -v test
```

**命中示例**:

```python
# ❌ print():无结构化字段,PII 明文,ELK parse 失败
print(f"user {user_id} logged in")
print("DEBUG:", order)
```

---

## §6 Auto-rewrite recipe

### 6.1 f-string → `%s` 占位符

```python
# ❌ before
logger.info(f"user {user_id} login")
logger.error(f"send mail failed, email={user.email}", exc_info=True)
logger.warning(f"device {device.serial_number} offline")

# ✅ after(SDK 看到原始对象,IDTYPE 反射生效)
logger.info("user %s login", user_id)
logger.error("send mail failed, email=%s", user.email, exc_info=True)
logger.warning("device %s offline", device.serial_number)
```

### 6.2 `.format()` → `%s` 占位符

```python
# ❌ before
logger.info("user {} login".format(user_id))
msg = "ticket {} assigned".format(ticket_id)
logger.warning(msg)

# ✅ after
logger.info("user %s login", user_id)
logger.warning("ticket %s assigned", ticket_id)
```

### 6.3 `+` 拼接 → `%s` 占位符

```python
# ❌ before
logger.info("user " + user_id + " login")
logger.error("send mail failed email=" + user.email)
logger.warning("device=" + str(device.serial_number) + " offline")

# ✅ after
logger.info("user %s login", user_id)
logger.error("send mail failed email=%s", user.email)
logger.warning("device=%s offline", device.serial_number)
```

### 6.4 `print()` → `logger`

```python
# ❌ before
print(f"user {user_id} logged in")
print("DEBUG:", order)

# ✅ after:普通路径用 info/debug;except 块用 exception(自动捕获 traceback)
import structlog
logger = structlog.get_logger()

logger.info("user %s logged in", user_id)
logger.debug("order %s", order)

# 异常路径
try:
    process_order(order)
except Exception:
    logger.exception("订单处理失败 order_id=%s", order.id)
    # → exception 字段自动写入 traceback,不要 print(traceback.format_exc())
```

> **注意**:`logger.exception()` 自动捕获当前 traceback 写入 `exception` 字段;`logger.error()` 不带 `exc_info=True` 会丢失 traceback。

### 6.5 dataclass 加 `metadata={"sensitive": ...}`

改写 `%s` 占位符后,还需确保传入的对象有 IDTYPE 标记,反射才真正生效:

```python
from dataclasses import dataclass, field

# ❌ before:无标记,user_id / email 原样输出
@dataclass
class User:
    user_id: str
    email: str
    name: str = ""

u = User(user_id="12345", email="alice@a4x.ai", name="alice")
logger.info("user %s login", u)
# → {"message": "user User(user_id='12345', email='alice@a4x.ai', name='alice') login"}

# ✅ after:加 metadata 标记,IDTYPE 反射自动包装
@dataclass
class User:
    user_id: str = field(metadata={"sensitive": "user_id"})
    email: str   = field(metadata={"sensitive": "email"})
    name: str    = ""      # 无标记,原样输出

u = User(user_id="12345", email="alice@a4x.ai", name="alice")
logger.info("user %s login", u)
# → {"message": "user {\"user_id\": \"[IDTYPE:user_id:12345]\", \"email\": \"[IDTYPE:email:alice@a4x.ai]\", \"name\": \"alice\"} login"}
```

metadata key 必须是字符串 `"sensitive"`,value 是 IDTYPE 类型名(小写,与 `spec/sensitive_types.json` 对齐)。**`typing.Annotated` 方案未采用,不要用。**

---

## §7 误报抑制

下列情况正则可能命中但不是真正的 PII 泄漏,**先不改,人工确认**:

| 情形 | 原因 | 建议 |
|---|---|---|
| `src/tests/` / `src/test_*.py` 下的测试文件 | 测试日志不上生产,风险低 | grep 加 `\| grep -v test` 或 review 后决定 |
| `logger.info(f"checking user_id mapping {table_name}")` | `user_id` 是上下文描述词,不是打值 | 人工确认后跳过 |
| exception message 含 PII(`e.args[0]` 里) | 根本原因在异常构造处 | 标记 `# TODO(pii-review):`,由业务开发处理 |
| `# comment: f"user {uid}"` 注释里的字符串 | 不执行 | 可忽略 |
| docstring 示例 `logger.info(f"...")` | 文档片段,不执行 | 可忽略;若在 doctest 里则检查 |
| `"url=/api/users/" + path_var` | URL 路径片段,`path_var` 不一定是 PII | 人工判断 `path_var` 含义 |
| CI lint `# noqa: G004` 豁免行 | 已人工确认合法 | 跳过 |

**操作规则**:疑似但语义不确定 → **不改,加 `# TODO(pii-review):` 注释**,让业务开发 review。false positive 自动改反而搞乱语义。

---

> **改写完必须跑**:`ruff check src/ && pytest` 绿灯后单独提交:`"chore: wrap PII scalars with %s placeholders and add dataclass sensitive metadata"`。不要与 middleware / SDK 接入逻辑的 commit 混在一起。
>
> **CI 双保险**:在 `pyproject.toml` 开启 ruff `G`(拦 f-string / `.format()` / 手动 `%`)+ `T201`(拦 `print()`),防止改写后回归。详见 [sdk-usage.md §3](sdk-usage.md#3-idtype--dataclass-metadata) 和 [common-pitfalls.md §5](common-pitfalls.md#5-f-string--format--拼接-pii-字段绕过-idtype-反射)。
