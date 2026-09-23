# SDK Usage — Python

> 本文件教 **`a4x-logger-sdk` Python 版怎么正确用**(接入步骤 / contextvars 约定 / dataclass metadata / OTel instrumentation / SDK 行为承诺 / 测试捕获)。不教怎么 recon 项目(看 [../project-recon.md](../project-recon.md))也不教接入流程(看 [execution.md](execution.md))。
>
> **基准版本**:`a4x-logger` 1.0.0 / Python 3.9+ / structlog 25.x
>
> **SDK 权威文档在** `<sdk>/docs/architecture/python/usage.md`。本文件是 **skill 侧精简版** + **每条内容都标注 verify 入口**(agent 引用前必须能在 SDK 仓库 grep / read 到)。

---

## §0 Verify 入口

### 包名 → 目录映射

| pip 包名 | 源码目录 | 主要内容 |
|---|---|---|
| `a4x-logger` (主包) | `<sdk>/python/a4x_logger/` | `setup_logging()` / `new_request_id()` 入口 |
| `a4x_logger.sensitive` | `<sdk>/python/a4x_logger/sensitive.py` | `format_idtype()` / `apply_masking()` / `apply_field_masking()` |
| `a4x_logger.testing` | `<sdk>/python/a4x_logger/testing/` | `log_capture` fixture / `LogCapture` / `assert_canonical_typed()` / `assert_log_emit()` |
| `a4x_logger.context_keys` | `<sdk>/python/a4x_logger/context_keys.py` | `CANONICAL_KEYS` 常量(12 个 canonical 字段名) |

### Verify 入口表

| 声称类型 | 命令 |
|---|---|
| `setup_logging` 签名 | `grep -n 'def setup_logging' <sdk>/python/a4x_logger/__init__.py` |
| `new_request_id` | `grep -n 'def new_request_id' <sdk>/python/a4x_logger/request_id.py` |
| dataclass metadata key `"sensitive"` | `grep -n 'metadata.get.*sensitive' <sdk>/python/a4x_logger/_placeholder.py` |
| `log_capture` fixture | `grep -n 'def log_capture' <sdk>/python/a4x_logger/testing/log_capture.py` |
| `LogCapture` 类 | `grep -n 'class LogCapture' <sdk>/python/a4x_logger/testing/log_capture.py` |
| `assert_canonical_typed` | `grep -n 'def assert_canonical_typed' <sdk>/python/a4x_logger/testing/canonical_fields.py` |
| `assert_log_emit` | `grep -n 'def assert_log_emit' <sdk>/python/a4x_logger/testing/canonical_fields.py` |
| `CANONICAL_KEYS` 列表 | `Read <sdk>/python/a4x_logger/context_keys.py` |
| Canonical 字段 / null-omit 契约 | `Read <sdk>/spec/log_format.json` |

### 捷径

首次接入任务 Read `<sdk>/docs/architecture/python/usage.md` + `<sdk>/spec/log_format.json` 各一次,80% 引用都覆盖到。

---

## §1 5 分钟接入

Python 项目 3 步接入,业务代码改动极小。

### Step 1:安装

**过渡期(Nexus 3.x 上线前)**:通过 GitLab Job Token + `GIT_CONFIG_*` insteadOf 模式直接拉,跟 Go 接入私有库完全同款。

**`requirements.txt`** 加一行(干净 URL,不带 token):

```
a4x-logger @ git+https://gitlab.addx.ai/CLOUD/a4x-logger-sdk.git@python/v1.0.0#subdirectory=python
```

**`.gitlab-ci.yml`** 加三个变量(全局 `variables:` 块,全 job 共享):

```yaml
variables:
  GIT_CONFIG_COUNT: "1"
  GIT_CONFIG_KEY_0: "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf"
  GIT_CONFIG_VALUE_0: "https://gitlab.addx.ai/"
```

GitLab Runner 启动 job 时自动注入 `${CI_JOB_TOKEN}`,**业务方无需维护任何 secret**。

**开发者本机**:

```bash
pip install 'git+ssh://git@gitlab.addx.ai/CLOUD/a4x-logger-sdk.git@python/v1.0.0#subdirectory=python'
```

> Docker build 场景(pip 在 Dockerfile build stage 里运行)的完整 `ARG CI_JOB_TOKEN` + multi-stage 方式见 `<sdk>/docs/architecture/python/usage.md` 附录 C。

### Step 2:初始化

在应用入口(**最早期,其他 import 之前**)调一次 `setup_logging()`:

```python
# main.py(应用入口最顶部)
from a4x_logger import setup_logging

setup_logging("user-service", config_file="logger.yaml")

# 之后才 import FastAPI / Flask / uvicorn 等
```

**签名**(`<sdk>/python/a4x_logger/__init__.py`):

```python
def setup_logging(
    service_name: Optional[str] = None,
    config_file: Optional[str] = None,
    level: Optional[str] = None,
    alias_mapping: Optional[Mapping[str, Sequence[str]]] = None,
    masks: Optional[Mapping[str, Sequence[str]]] = None,
    max_message_length: Optional[int] = None,
    on_error: Optional[str] = None,   # "log_raw" | "redact" | "drop"
    otel_auto_extract: bool = True,
) -> None: ...
```

配置优先级:**programmatic args > `config_file` > 默认值**。`setup_logging` 绝不 raise(见 §5 panic safety)。

### Step 3:打日志

任何模块里:

```python
import structlog

logger = structlog.get_logger()

logger.info("查询用户 %s", user_id)      # %s 占位符 — 唯一推荐写法
logger.exception("数据库失败")           # except 块里,自动捕获 traceback → exception 字段
```

**FastAPI 完整示例**:

```python
from a4x_logger import setup_logging
import structlog
from fastapi import FastAPI

setup_logging("user-service", config_file="logger.yaml")

app = FastAPI()
# SDK 不提供成品 middleware;业务自实现 ASGI middleware(定义见 §8.1)
app.add_middleware(MyBusinessMiddleware)

logger = structlog.get_logger()

@app.get("/users/{user_id}")
async def get_user(user_id: int):
    logger.info("查询用户 %s", user_id)
    return {"user_id": user_id}
```

**Flask 完整示例**:

```python
from a4x_logger import setup_logging
import structlog
from flask import Flask

setup_logging("user-service", config_file="logger.yaml")

app = Flask(__name__)
# SDK 不提供成品 middleware;业务自实现 WSGI middleware(定义见 §8.2)
app.wsgi_app = MyBusinessWSGIMiddleware(app.wsgi_app)

logger = structlog.get_logger()

@app.route("/users/<int:user_id>")
def get_user(user_id):
    logger.info("查询用户 %s", user_id)
    return {"user_id": user_id}
```

**启动时 `logger_init` 事件**:`setup_logging()` 自动 emit 一条结构化事件(ADR-10),含 `event="logger_init"` / `sdk_version` / `schema_version` / `language="python"` / `service` / `instance`。这是 observability 平台关联业务日志 schema 版本用的,不是 bug。

**第三方框架日志自动 JSON 化**:`setup_logging()` 同时桥接 stdlib `logging`——uvicorn / gunicorn / requests / urllib3 / SQLAlchemy 等所有 `logging.getLogger(...)` 出来的 logger 全部自动走同一 JSON 管线,**业务不用为任何第三方库单独配 handler/formatter**。

---

## §2 contextvars 写入方约定

SDK Format 层是 **contextvars 的只读消费者**:每条日志输出 JSON 时,SDK 从当前协程/线程的 contextvars 读取 12 个 canonical 字段写入 JSON。**SDK 不会自己往 contextvars 里写任何业务字段**——每个字段必须由"写入方"主动调用 `structlog.contextvars.bind_contextvars(...)` 才会出现在 JSON 里。

### 12 个 canonical 字段写入方表

| 字段 | 写入方 | 说明 |
|---|---|---|
| `trace_id` | OTel 自动(见 §4) | W3C 32-hex |
| `span_id` | OTel 自动(见 §4) | W3C 16-hex |
| `request_id` | 业务 middleware | UUID(推荐 32-hex 无连字符);可用 `new_request_id()` 生成 |
| `user_id` | 业务 middleware(从 JWT / header) | 当前用户 ID |
| `tenant_id` | 业务 middleware | 多租户系统的租户 ID |
| `account_id` | 业务代码(按需) | 账户 ID |
| `serial_number` | 业务代码 | 设备 SN |
| `model_no` | 业务代码 | 设备型号 |
| `firmware_version` | 业务代码 | 设备固件版本 |
| `device_msg_src` | 业务代码 | 推荐值:`device` / `cloud` / `app` / `ble` / `mqtt` |
| `session_id` | 业务代码(按需,设备场景) | 设备本地操作会话 ID |
| `task_name` | 业务代码(定时任务 / 批处理) | 任务/作业名 |

### 关键边界

**`trace_id` / `span_id` 由 OTel 接管**:部署 OTel Python instrumentation 后,OTel SDK 自动把 trace 上下文写入 contextvars,**业务代码不要再手动 `bind_contextvars(trace_id=...)`**。

**没人写入会怎样**:未绑定的 canonical 字段按 null-omit 规则直接从 JSON 中省略,key 不出现。日志仍是合法 JSON,不影响其他字段输出:

```python
# 最简接入:只调 setup_logging,没写任何 middleware,也没接 OTel
setup_logging("user-service")
logger.info("hello")
# → {"timestamp":"...","level":"INFO","service":"user-service","instance":"...",
#    "logger":"...","thread":"MainThread","message":"hello"}
#
# trace_id / user_id 等 12 个 canonical 字段全部不出现 — null-omit 规则
```

**空字符串 vs 缺失是两件事**:`bind_contextvars(user_id="")` 表示"已确认匿名",SDK 输出 `"user_id": ""`。不调用 `bind_contextvars` 才是 null-omit(key 不出现)。

**contextvars 在 async 协程链中天然传播**:Python 的 `contextvars` 无需额外操作就在 await 链下游传播 `trace_id` / `user_id` 等字段——业务不需要手动传参。

### 同字段单写入方红线

业务项目选以下三种风格之一,**同一字段只能有一个写入方**:

1. **在已有 AuthMiddleware 里 bind**:JWT 解析的地方顺手 `bind_contextvars(user_id=..., tenant_id=...)`,不要额外再写一个 LoggingContextMiddleware。
2. **专用 LoggingContextMiddleware**:网关注 header,服务内只做 header → contextvars 映射。
3. **完全不写 middleware**:仅依赖 OTel 自动写 `trace_id` / `span_id`,业务字段不需要(适合纯查询 / 批处理)。

**非 canonical key 不会自动入 JSON**:只有上表中的 key 会被 SDK 从 contextvars 读出。通过 `bind_contextvars(custom_key=...)` 绑定的非 canonical key 不会出现在 JSON 中。

---

## §3 IDTYPE / dataclass metadata

SDK 通过 `@dataclass` + `field(metadata={"sensitive": "<idtype>"})` 标记敏感字段。这是 Python SDK **唯一正式支持的 IDTYPE 标记方式**(`typing.Annotated` 方案未采用,不要用)。

### dataclass 标记写法

```python
from dataclasses import dataclass, field

@dataclass
class User:
    user_id: str = field(metadata={"sensitive": "user_id"})
    email: str = field(metadata={"sensitive": "email"})
    name: str = ""      # 无标记,原样输出(除非命中 key-triggered 规则)
```

metadata key 必须是字符串 `"sensitive"`,value 是 IDTYPE 类型名(小写,与 spec/sensitive_types.json 对齐)。

### 反射闭环

IDTYPE 反射**只在占位符路径**(`*args` 位置参数)生效:

```python
u = User(user_id="12345", email="alice@a4x.ai", name="alice")

# ✅ 推荐:位置参数 + %s 占位符 → IDTYPE 自动反射
logger.info("user %s login", u)
# → {"message":"user {\"user_id\": \"[IDTYPE:user_id:12345]\", \"email\": \"[IDTYPE:email:alice@a4x.ai]\", \"name\": \"alice\"} login"}
```

**机制**(`<sdk>/python/a4x_logger/_placeholder.py`):SDK 在 structlog processor 链内遍历 `dataclasses.fields(obj)`,读 `f.metadata.get("sensitive")`,把有标记的字段值包装成 `[IDTYPE:{type}:{value}]`,然后 `template % tuple(rendered_args)` 填充模板。

### 手动调用(次选)

需要在 message 外显式格式化时可用 `format_idtype`:

```python
from a4x_logger.sensitive import format_idtype

masked = format_idtype("user_id", "12345")
# → "[IDTYPE:user_id:12345]"
logger.info("操作用户 user_id=%s", masked)
```

### 嵌套 / 集合 / Optional corner case

| 情况 | 行为 |
|---|---|
| 嵌套 dataclass(`User.address` 是另一个 dataclass) | SDK 递归读 `fields()`,嵌套 dataclass 里的 `metadata={"sensitive": ...}` 同样生效 |
| `list[User]` | 每个元素单独反射,标记生效 |
| `Optional[str]` 字段,值为 `None` | null-omit 规则:该字段从 JSON 省略 |
| 标量字段(无标记) | 原样序列化 |
| pydantic `Field(..., json_schema_extra={"sensitive": "<idtype>"})` | ⚠️ SDK doc 声称(`<sdk>/docs/architecture/python/overview.md §2.1`),但 SDK 源 `a4x_logger/_placeholder.py` 当前**只有 dataclass 路径,无 pydantic 处理**(2026-05 verify)。**接入前必须 grep `<sdk>/python/a4x_logger/_placeholder.py` 确认有 pydantic 分支,或用 LogCapture 实测**。否则按 dataclass 路径走。 |

### 禁止预格式化 PII

```python
u = User(user_id="12345", email="alice@a4x.ai", name="alice")

# ❌ f-string: SDK 入口前 u 已变成字符串,IDTYPE 反射失效,PII 明文落盘
logger.info(f"user {u} login")

# ❌ .format(): 同理
logger.info("user {} login".format(u))

# ❌ 手动 % 拼接: 同理
logger.info("user %s login" % u)

# ❌ str 拼接: 同理
logger.info("user " + str(u))

# ✅ 正确: template + 原始对象给 SDK,由 SDK 内部做 %s 替换
logger.info("user %s login", u)
```

f-string 是 Python 日常肌肉记忆,**必须**通过 lint 规则在 CI 强制拦截:

```toml
# pyproject.toml (ruff)
[tool.ruff.lint]
select = ["G"]  # flake8-logging-format
# G004: logging-fstring-interpolation
# G001: logging-string-format
# G002: logging-percent-format
```

---

## §4 OTel Python instrumentation

### 安装

```bash
# FastAPI / Starlette
pip install opentelemetry-instrumentation-fastapi opentelemetry-sdk

# Flask
pip install opentelemetry-instrumentation-flask opentelemetry-sdk

# gRPC server
pip install opentelemetry-instrumentation-grpc opentelemetry-sdk
```

`opentelemetry-api` 不进 SDK 主依赖——SDK 运行时 `try import` 自动检测。业务没装 OTel 时 `trace_id` / `span_id` 按 null-omit 规则省略,其他功能不受影响。

### `trace_id` / `span_id` 自动写入 contextvars

SDK 的 `otel_auto_extract`(默认 `True`)在 structlog processor 链中自动读当前 OTel span context 并把 `trace_id` / `span_id` 写入日志 JSON——**业务代码不要再手动 `bind_contextvars(trace_id=...)`**。

```python
from a4x_logger import setup_logging
import structlog
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

setup_logging("user-service", config_file="logger.yaml")
# otel_auto_extract=True(默认):SDK 自动从 OTel span 读 trace_id/span_id

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)  # OTel 负责 span 生命周期
app.add_middleware(MyBusinessMiddleware)  # 业务 middleware 只绑定业务字段

logger = structlog.get_logger()
```

### 不在 SDK 范围的事

以下全部交给 OTel(ADR-05 / ADR-08),SDK 不提供任何拦截器:

- **出站 HTTP 请求 trace 透传**(`traceparent` header 注入)——由 `opentelemetry-instrumentation-requests` / `-httpx` 自动完成
- **Kafka trace 透传**——由 OTel Kafka instrumentation 完成
- **gRPC client 端 trace 透传**——由 `opentelemetry-instrumentation-grpc` 完成

### 没有 OTel 时

`setup_logging(otel_auto_extract=False)` 可显式禁用 OTel auto-extract。`trace_id` / `span_id` 按 null-omit 规则从 JSON 省略,其他字段和脱敏正常工作。

---

## §5 SDK 行为承诺

### null-omit

contextvars 中某个 canonical key 不存在时,SDK JSON 输出中该字段**不出现**(key 直接省略,不输出 `null` / 空字符串)。依据 `spec/log_format.json emission_policy.unified_rule`。

```python
# trace_id 没 bind → 日志 JSON 里没有 trace_id 字段
structlog.contextvars.bind_contextvars(user_id="u-100")
logger.info("处理请求")
# → {"level":"INFO","message":"处理请求","user_id":"u-100","service":"my-service",...}
#   没有 trace_id / span_id / tenant_id 等未绑定的字段
```

`""` 是合法值:显式 `bind_contextvars(user_id="")` 表示"已确认匿名",SDK 仍输出 `"user_id": ""`。

### `%s` 占位符(placeholder_input_invariant)

Python 用 stdlib logging 原生的 `%s` / `%d` 占位符。SDK 必须看到原始对象才能反射 dataclass metadata。对应 `spec/log_format.json emission_policy.placeholder_input_invariant`(spec 1.1.0)。

```python
# ✅ SDK 看到 user 对象,IDTYPE 反射生效
logger.info("user %s login", user)

# ❌ f-string: SDK 只看到字符串,IDTYPE 失效,PII 明文落盘
logger.info(f"user {user} login")
```

**参数数量不匹配时的 SLF4J 对齐规则**:

```python
logger.info("user %s", user, "extra")       # 多余 args 静默忽略
logger.info("user %s from %s", user)        # 少 args 保留字面量 → "user alice from %s"
```

### 异常日志

```python
try:
    process_order(order)
except Exception:
    logger.exception("订单处理失败 order_id=%s", order.id)
# → {"level":"ERROR","message":"订单处理失败 order_id=ORD-001",
#    "exception":"Traceback (most recent call last):...",...}
```

`logger.exception()` 是 stdlib logging API,自动捕获当前 traceback 写入 `exception` 字段。`logger.error()` 不带 `exc_info=True` 会丢失 traceback。

### kwargs 结构化字段(次选)

大多数场景**不需要** kwargs。仅当字段确实需要 ELK / OpenSearch 独立索引可查时考虑:

```python
# ⚠️ 次选:kwargs 让 order_id 成为独立 JSON key,可在 ELK 里精确过滤
logger.info("处理订单", order_id="ORD-001")
# → {"message":"处理订单","order_id":"ORD-001",...}
```

**注意**:kwargs 路径不走 IDTYPE 反射——只有 `*args` 位置参数路径才能触发 dataclass metadata 反射。

### Dynamic Field Conflict(DFC)

动态字段名落在 SDK 管理字段集合里时静默丢弃:

```python
logger.info("test", order_id="ORD-001")   # ✅ 透传
logger.info("test", trace_id="fake")       # ❌ DFC 静默丢弃(reserved)
logger.info("test", level="CRITICAL")      # ❌ DFC 静默丢弃(reserved)
```

### panic safety(ADR-15)

SDK 承诺:**任何 SDK 内部 bug(脱敏正则崩溃 / 配置加载失败 / OTel 不可用等)都不会逃逸到业务代码**。

| 失败位置 | SDK 行为 |
|---|---|
| masking pipeline 抛异常 | 按 `on_error` 配置(`log_raw` / `redact` / `drop`)降级 |
| IDTYPE 反射期抛异常 | 跳过 IDTYPE 包装,raw 值原样输出 |
| OTel 不可用 | `trace_id` / `span_id` 按 null-omit 省略,其余不受影响 |
| `setup_logging()` 配置文件加载失败 | 降级到默认值 + stderr WARN,绝不 raise |

✅ `logger.info(...)` / `logger.error(...)` 等调用**永远不抛异常**。

---

## §6 测试捕获

### log_capture fixture

来源:`<sdk>/python/a4x_logger/testing/log_capture.py`

`log_capture` 是 pytest fixture,使用时直接在测试函数参数列表声明,**不需要** `import` fixture(通过 `conftest.py` 注册):

```python
# conftest.py(项目测试根目录)
from a4x_logger.testing.log_capture import log_capture  # noqa: F401
```

```python
# test_example.py
from a4x_logger import setup_logging
import structlog

def test_emit_canonical_fields(log_capture):
    setup_logging("test-service")
    structlog.contextvars.bind_contextvars(user_id="u-100", request_id="abc123")

    logger = structlog.get_logger()
    logger.info("处理请求")

    line = log_capture.last()
    assert line["level"] == "INFO"
    assert line["message"] == "处理请求"
    assert line["user_id"] == "u-100"
    assert "tenant_id" not in line          # null-omit:未 bind 的字段不出现
```

### LogCapture API

| 方法 | 说明 |
|---|---|
| `log_capture.lines()` | 所有捕获的 JSON 行,已解析为 `list[dict]` |
| `log_capture.last()` | 最后一条日志的 `dict`;无捕获时抛 `AssertionError` |
| `log_capture.clear()` | 清空 buffer(多阶段断言时用) |

`log_capture` fixture 自动清空 contextvars(before / after),防止跨测试泄漏。每个测试函数都需要调一次 `setup_logging()` 重新配置 SDK。

### 断言工具:`assert_canonical_typed` / `assert_log_emit`

来源:`<sdk>/python/a4x_logger/testing/canonical_fields.py`

```python
from a4x_logger.testing.canonical_fields import assert_canonical_typed, assert_log_emit

def test_canonical_fields(log_capture):
    setup_logging("test-service")
    structlog.contextvars.bind_contextvars(user_id="u-100")
    structlog.get_logger().info("hello")

    line = log_capture.last()

    # 断言:出现的 canonical 字段都是 string 类型
    assert_canonical_typed(line)

    # 断言:expected 列出的字段值匹配;未列出的 canonical 字段不出现(null-omit)
    assert_log_emit(line, {
        "level": "INFO",
        "message": "hello",
        "user_id": "u-100",
        "tenant_id": {"absent": True},   # sentinel: 断言此字段不出现
    })
```

### 测试 masking

```python
def test_masking_phone(log_capture):
    setup_logging(
        "test-service",
        masks={"phone": [r"\b1[3-9]\d{9}\b"]},
    )
    structlog.get_logger().info("user phone is 13812345678")

    actual = log_capture.last()
    assert "13812345678" not in actual["message"]
```

### 测试 IDTYPE 反射

```python
from dataclasses import dataclass, field

@dataclass
class User:
    user_id: str = field(metadata={"sensitive": "user_id"})
    name: str = ""

def test_idtype_reflection(log_capture):
    setup_logging("test-service")
    u = User(user_id="12345", name="alice")
    structlog.get_logger().info("user %s", u)

    msg = log_capture.last()["message"]
    assert "[IDTYPE:user_id:12345]" in msg
    assert "alice" in msg   # 无标记字段原样出现
```

---

## §7 项目层 convention vs SDK 硬规则

| SDK 硬规则(agent 必须遵守) | 项目层 convention(推荐,migration 不强制) |
|---|---|
| `%s` 占位符传原始对象,禁止预格式化 PII | `action=<name> result=<state>` message 格式 |
| 业务 middleware 请求结束时 `unbind_contextvars(*CANONICAL_KEYS)`(防线程池/协程复用泄漏) | canonical key 命名统一(`user_id` 不是 `uid`) |
| contextvars key 必须是 canonical 名(或配了 `alias_mapping`) | Info/Warn/Error level 选型规范 |
| dataclass `field(metadata={"sensitive": "<idtype>"})` 标注 PII 字段 | message 里是否加 `duration_ms` |
| 非 canonical contextvars key 走 kwargs 动态字段,不走 `bind_contextvars` | action 命名 snake_case verb_noun |
| `otel_auto_extract=True`(默认)时不手动 bind `trace_id` / `span_id` | 日志 message 语言(中文 / 英文) |
| ruff `G` 规则(`G004` fstring-interpolation)在 CI 强制拦截 | SonarQube `pythonsecurity:S5145` ignore 配置(见 SDK doc 附录 D) |

> **协程复用泄漏**:Python asyncio worker 线程可能被多个请求复用。中间件必须在 `finally` 块里 `unbind_contextvars(*CANONICAL_KEYS)`,不能只在 `try` 末尾——这与 Java MDC.clear() 的强制要求完全对应。

**项目层 convention** 看 [../log-style-guide.md](../log-style-guide.md)。**migration 时不强行 retrofit 历史代码**。

---

## §8 ASGI / WSGI Middleware 参考实现

> Python SDK **不提供**成品 ASGI/WSGI middleware 或 gRPC interceptor。本节给出三份参考实现,业务项目可直接拷贝到自己仓库按需修改。
>
> **绑定原则**:只在有真实值时调用 `bind_contextvars`——span context 无效时不绑 `trace_id`/`span_id`;header 缺失时不绑对应字段。SDK 输出 JSON 时未绑定的字段自动 omit。
>
> **同字段单写入方**:如果项目已有 AuthMiddleware 在 bind 某字段,本参考实现对应字段直接跳过,不要双写。

### 8.1 ASGI Middleware 参考实现(FastAPI / Starlette)

```python
# 业务项目自行实现,可直接拷贝此模板按需修改
from opentelemetry import trace
import structlog

from a4x_logger import new_request_id
# import 而非 inline tuple:SDK 加新 canonical 字段时业务模板自动跟进,不会漂移
from a4x_logger.context_keys import CANONICAL_KEYS


class MyBusinessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # null-omit 规则:只把有真实值的字段加入 bindings;缺失的 key 不绑定,
        # SDK 输出时该字段在 JSON 中自动 omit(key 不出现).
        # 显式想输出 "key": "" 才传空字符串(如已确认匿名用户 → user_id="").
        bindings = {
            "request_id": new_request_id(),   # 总是有值(middleware 生成)
            "device_msg_src": "cloud",         # 总是有值(本服务的恒定来源)
        }

        # OTel 当前 span:只在 span 有效时绑定 trace_id/span_id
        span_ctx = trace.get_current_span().get_span_context()
        if span_ctx.is_valid:
            # f-string ok here: 格式化后赋给 dict,不是直接传 logger.*();G004 只检查 logger.*() 调用
            bindings["trace_id"] = f"{span_ctx.trace_id:032x}"
            bindings["span_id"] = f"{span_ctx.span_id:016x}"

        # 业务字段:从认证上下文 / header 抽取,缺失就不绑
        if (user_id := extract_user_id(scope)) is not None:
            bindings["user_id"] = user_id
        if (tenant_id := extract_tenant_id(scope)) is not None:
            bindings["tenant_id"] = tenant_id

        # 设备 header:缺失就不绑
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        for header_name, key in (
            ("x-serial-number",    "serial_number"),
            ("x-device-model",     "model_no"),
            ("x-firmware-version", "firmware_version"),
        ):
            if (value := headers.get(header_name)) is not None:
                bindings[key] = value

        # 先 unbind 所有 canonical key 防止协程复用时残留旧值
        structlog.contextvars.unbind_contextvars(*CANONICAL_KEYS)
        structlog.contextvars.bind_contextvars(**bindings)

        try:
            await self.app(scope, receive, send)
        finally:
            structlog.contextvars.unbind_contextvars(*CANONICAL_KEYS)
```

注册:

```python
app = FastAPI()
app.add_middleware(MyBusinessMiddleware)
```

### 8.2 WSGI Middleware 参考实现(Flask / 传统 Django)

```python
from opentelemetry import trace
import structlog

from a4x_logger import new_request_id
# import 而非 inline tuple:SDK 加新 canonical 字段时业务模板自动跟进,不会漂移
from a4x_logger.context_keys import CANONICAL_KEYS


class MyBusinessWSGIMiddleware:
    """业务项目自行实现。同 ASGI:null-omit 规则下只绑定有真实值的字段。"""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        bindings = {
            "request_id": new_request_id(),
            "device_msg_src": "cloud",
        }

        span_ctx = trace.get_current_span().get_span_context()
        if span_ctx.is_valid:
            # f-string ok here: 格式化后赋给 dict,不是直接传 logger.*();G004 只检查 logger.*() 调用
            bindings["trace_id"] = f"{span_ctx.trace_id:032x}"
            bindings["span_id"] = f"{span_ctx.span_id:016x}"

        if (user_id := extract_user_id_wsgi(environ)) is not None:
            bindings["user_id"] = user_id
        if (tenant_id := extract_tenant_id_wsgi(environ)) is not None:
            bindings["tenant_id"] = tenant_id

        for env_key, key in (
            ("HTTP_X_SERIAL_NUMBER",    "serial_number"),
            ("HTTP_X_DEVICE_MODEL",     "model_no"),
            ("HTTP_X_FIRMWARE_VERSION", "firmware_version"),
        ):
            if (value := environ.get(env_key)) is not None:
                bindings[key] = value

        structlog.contextvars.unbind_contextvars(*CANONICAL_KEYS)
        structlog.contextvars.bind_contextvars(**bindings)
        try:
            return self.app(environ, start_response)
        finally:
            structlog.contextvars.unbind_contextvars(*CANONICAL_KEYS)
```

注册:

```python
app = Flask(__name__)
app.wsgi_app = MyBusinessWSGIMiddleware(app.wsgi_app)
```

### 8.3 定时任务 / 后台任务

定时任务没有 HTTP 请求入口,在任务函数里手动 bind contextvars:

```python
from a4x_logger import new_request_id
import structlog

def daily_report():
    structlog.contextvars.bind_contextvars(
        task_name="daily-report",
        request_id=new_request_id(),
    )
    try:
        logger.info("开始生成日报")
        process_all_orders()
        logger.info("日报完成")
    finally:
        structlog.contextvars.unbind_contextvars("task_name", "request_id")
```

### 8.4 完整 logger.yaml 参考

```yaml
a4x-logger:
  service-name: user-service      # 默认读 env:SERVICE_NAME
  level: INFO                     # DEBUG | INFO | WARNING | ERROR
  max-message-length: 32768       # 默认 32768(32KB),最小 256

  # context aliases(迁移期兼容,新项目跳过)
  context:
    aliases:
      firmware_version:
        - firmwareId

  # Key-triggered 脱敏(ADR-14):rule key 既是规则名也是 message literal trigger
  # SDK 不内置任何规则,项目要哪些必须显式配
  sensitive:
    enabled: true
    on-error: log_raw             # log_raw | redact | drop
    masks:
      phone:
        - "\\b1[3-9]\\d{9}\\b"
      email:
        - "\\b[\\w.+-]+@[\\w-]+\\.[\\w.]+\\b"
      api-token:
        - "(sk-[a-zA-Z0-9]{32})"
      bearer:
        - "Bearer\\s+[A-Za-z0-9._~+/-]+=*"
```

---

> **版本提示**:本文件基于 `a4x-logger` 1.0.0。版本升级时以 `<sdk>/docs/architecture/version-matrix.md` 为权威,接入步骤不变。
