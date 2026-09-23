# Case Study — python-fastapi-demo

> **本 case-study 基于 SDK monorepo `examples/python-fastapi-demo`**,demo-driven baseline。
> 真实业务的 quirks(大型多 router 项目 / Django 替代 / Celery worker / FastAPI BackgroundTasks
> / uvicorn 多进程 / gRPC 接入等)等首个 Python 真接入服务补 production case-study。
>
> **类比 Go 的 naturehood.md**(production case-study,21 commits)— 本文件是 baseline(demo)。
> **类比 Java 的 java-spring-demo.md** — 同款 demo-driven 结构,Python 版。

| 维度 | 值 |
|---|---|
| 类型 | demo-driven baseline |
| 项目 | SDK monorepo `examples/python-fastapi-demo` |
| 规模 | 1 main.py + 1 logger.yaml,约 110 行代码 |
| 接入路径 | `setup_logging` + 业务自实现 ASGI middleware(SDK 不提供成品) |
| SDK 安装 | `pip install -e ../../python`(本地 editable 引用 monorepo python 目录) |
| Python | 3.9+ / structlog 25.x |
| 验证场景 | 3 个 curl 场景(happy path / masking demo / minimal) |

---

## §1 项目规模与依赖

### 1.1 文件清单

```
examples/python-fastapi-demo/
├── main.py       — FastAPI app + ASGI middleware + 3 个 endpoint(~110 行)
└── logger.yaml   — SDK 配置:masking rules + alias + max-message-length
```

无 `src/test/`(demo 无单元测试,验证靠 curl 场景)。

### 1.2 运行时依赖

| 包 | 说明 |
|---|---|
| `fastapi` | HTTP framework |
| `uvicorn` | ASGI server |
| `a4x_logger`(本地 editable) | SDK 主包:提供 `setup_logging` / `new_request_id` / `CANONICAL_KEYS` |
| `structlog` | 由 SDK 传递引入;业务直接调 `structlog.get_logger()` / `structlog.contextvars.*` |

SDK 传递引入:structlog 25.x / stdlib logging bridge(uvicorn 日志自动 JSON 化,不需要额外配)。

### 1.3 logger.yaml 关键配置

```yaml
a4x-logger:
  service-name: demo-svc
  level: INFO
  max-message-length: 32768

  context:
    aliases:
      firmware_version:
        - firmwareId   # 迁移期兼容示范:旧 header firmwareId → canonical firmware_version

  sensitive:
    enabled: true
    on-error: log_raw
    masks:
      phone:
        - "\\b1[3-9]\\d{9}\\b"                 # 中国大陆 11 位手机号
      email:
        - "\\b[\\w.+-]+@[\\w-]+\\.[\\w.]+\\b"
      api-token:
        - "(sk-[a-zA-Z0-9]{32})"               # OpenAI 风格 API key
      bearer:
        - "Bearer\\s+[A-Za-z0-9._~+/-]+=*"
      id-card:
        - "\\b\\d{17}[\\dXx]\\b"               # 18 位身份证
```

> **mask rule 设计要点**:SDK 不内置任何规则(ADR-14 key-triggered 模型)。
> rule 的 key(`phone` / `email` 等)既是规则名也是 message 里的字面量 trigger——
> message 里没有 `"phone"` 这个子串时 phone 规则不触发。
> 完整中国 PII 规则推荐模板见 `docs/architecture/config-reference.md`。

### 1.4 main.py 结构

```python
from a4x_logger import new_request_id, setup_logging
from a4x_logger.context_keys import CANONICAL_KEYS

setup_logging(service_name="demo-svc", config_file=_CONFIG, level="INFO")  # 入口最顶部
logger = structlog.get_logger()

app = FastAPI()
app.add_middleware(A4xLogContextMiddleware)   # 业务自实现 ASGI middleware

@app.get("/orders/{order_id}")  # happy path — 有 trace_id / user_id
@app.get("/contact-info")       # masking demo — phone + email 规则触发
@app.get("/health")             # minimal — 只有自动生成的 request_id
```

---

## §2 接入轨迹(对照 python/execution.md 7 步)

> 完整 7 步规范见 [../python/execution.md](../python/execution.md)。
> demo 是 greenfield(非迁移),部分步骤 n/a。

| Step | 说明 | demo 实际 | 备注 |
|---|---|---|---|
| 0 baseline | `pytest -x` 记失败数 + smoke curl 脚本 | n/a | greenfield demo,无存量 endpoint,跳过 smoke discover |
| 1 install + `setup_logging` | pip 安装 + `setup_logging` 入 main 入口 | ✅ | `pip install -e ../../python`(monorepo 本地引用);`setup_logging("demo-svc", config_file=_CONFIG)` 在所有 import 之前 |
| 2 ASGI middleware | `bind_contextvars` 写入 canonical 字段 | ✅ 自实现 | `A4xLogContextMiddleware`:4 个 header → contextvars + `new_request_id()` 兜底;finally 块 `unbind_contextvars(*CANONICAL_KEYS)`;SDK 不提供成品 |
| 3 dataclass metadata | `field(metadata={"sensitive": ...})` 标注 | n/a | demo 无 PII dataclass;endpoint 只传标量 `order_id`(str),走 `%s` 占位符路径 |
| 4 PII 写法纠错 | f-string / `.format` / `+` 拼接 → `%s` 占位符 | n/a(greenfield) | demo 从一开始就是 `logger.info("fetching order %s", order_id)` 正确写法 |
| 5 测试改写 | `caplog` → SDK `log_capture` fixture | n/a | demo 无 `src/test/`;验证靠 curl |
| 6 CI/Docker | GIT_CONFIG insteadOf + Dockerfile 双 stage | n/a | demo 不独立打包;SDK monorepo CI 覆盖;规范见 [../python/execution.md §6](../python/execution.md) |
| 7 文档 | 接入设计 doc / migration doc | ✅(demo 基线) | `README.md` = demo baseline doc;curl 场景全列;本文件是 skill 侧的深度拆解 |

**主动决策说明**:

- **Step 2 middleware 模式选择**:demo 使用"写法 B"(专用 `A4xLogContextMiddleware`,网关注 header,服务内做 header → contextvars 映射)。真实业务如果已有 `AuthMiddleware` / `JwtMiddleware` 在 `bind_contextvars(user_id=...)`,应在自家 middleware 里扩展,**不要额外挂一个参考实现**——避免同字段双写入(见 [../python/execution.md §2](../python/execution.md) 同字段单写入方红线)。

- **Step 3 n/a 的原因**:demo 的 endpoint 接收 `order_id: str` 标量,没有带 PII 字段的 dataclass,所以 IDTYPE 反射路径未被覆盖。真实业务如果有 `User` / `Order` dataclass 含敏感字段,需要补 `field(metadata={"sensitive": "<idtype>"})` 标注(见 [../python/sdk-usage.md §3](../python/sdk-usage.md))。

- **`setup_logging` 位置**:`_CONFIG = str(Path(__file__).with_name("logger.yaml"))` 这个路径构造在 main.py 最顶部,确保 `setup_logging` 在任何 FastAPI / Starlette import 之前调用。如果 `setup_logging` 位置不当(比如放在 module-level 但 import 了别的模块又先 `import structlog`),structlog 全局 config 可能被覆盖——这是 Python SDK 接入最常见的 import 顺序 bug。

---

## §3 验证场景表

服务跑在 `localhost:8000`。3 个 curl 场景对应 3 个 endpoint。

| # | 场景 | curl 命令 | 期望 JSON 输出要点 |
|---|---|---|---|
| 1 | **Happy path — header → canonical fields** | `curl -H 'X-Trace-Id: 4bf92f3577b34da6a3ce929d0e0e4736' -H 'X-User-Id: 12345' http://localhost:8000/orders/ORD-001` | `trace_id:"4bf92f3577b34da6a3ce929d0e0e4736"`、`user_id:"12345"` 出现在顶层;`message:"fetching order ORD-001"`;`request_id` 自动生成(UUID 兜底);`service:"demo-svc"` |
| 2 | **Masking demo — key-triggered 脱敏** | `curl http://localhost:8000/contact-info` | 第一条:message `"user phone is 13812345678 contacted us"` → `"user phone is *** contacted us"`;第二条:message `"user email is alice@example.com requested help"` → `"user email is *** requested help"`;**规则触发条件:message 必须包含 `"phone"` / `"email"` 字面量子串** |
| 3 | **Minimal — 无 header** | `curl http://localhost:8000/health` | 只有自动生成的 `request_id`(UUID);`trace_id` / `user_id` 等未传 header 的 canonical 字段 **不出现在 JSON 中**(null-omit 规则,key 直接省略,不是 `null` 也不是 `""`) |

**SDK 输出字段集合**(每条日志 JSON 的固定字段):
`timestamp` / `level` / `logger` / `thread` / `service` / `instance` / `message`

加上当前请求 contextvars 中已 bind 的 canonical 字段(`trace_id` / `user_id` / `request_id` 等)。未 bind 的字段按 null-omit 规则从 JSON 省略。

**启动时验证**:服务启动会自动 emit 一条 `logger_init` 事件(ADR-10),含 `event="logger_init"` / `sdk_version` / `schema_version` / `language="python"`。

**场景 2 补充说明(key-triggered masking)**:rule 的 key `"phone"` / `"email"` 是字面量触发器——`"User dialed 13812345678"` 不含 `"phone"` 子串,phone 规则不触发,号码明文落盘。demo README 明确标注了这个 ADR-14 行为。

---

## §4 demo 没覆盖的真实业务 quirks(待 production case-study 补)

待首个 Python 真接入服务的 owner 补充具体上下文:

- [ ] **大型多 router 项目的 `setup_logging` 调用时机**:应用入口(`main.py` / `app.py`)拆分成多 module 时,`setup_logging` 必须在其他任何 `structlog` / `logging` import 副作用之前调用——import 顺序不对会导致 structlog 全局 config 被覆盖,JSON 格式管线失效

- [ ] **Django 接入**:无 FastAPI ASGI middleware 概念;`setup_logging` 放在 `AppConfig.ready()`;canonical 字段写入用 Django middleware(`process_request` / `process_response`),替代 ASGI `bind_contextvars` 写法;可搭配 `django-structlog` 减少模板代码,但注意双写入方风险

- [ ] **Celery worker contextvars 跨进程不传**:Celery 用进程池,contextvars 不跨进程传播;推荐用 Celery signals(`task_prerun` + `task_postrun`)+ `bind_contextvars(task_name=..., request_id=...)` 手动注入上下文;分布式 trace 透传需通过 task `headers` 字段传 `traceparent`

- [ ] **FastAPI BackgroundTasks contextvars 行为**:FastAPI `BackgroundTasks` 在 response 发出后运行,此时 middleware 已经 `unbind_contextvars`——background task 无法继承请求的 canonical 字段;需要在 task 函数里手动 `bind_contextvars` + `try/finally unbind`(见 [../python/sdk-usage.md §8.3](../python/sdk-usage.md))

- [ ] **uvicorn `--workers N` 多进程 + contextvars 边界**:多 worker 进程之间 contextvars 完全隔离(不共享);每个 worker 进程独立调用 `setup_logging`(由 uvicorn fork 时自动触发);日志输出在各进程 stdout 合并——这是预期行为,不是 bug

- [ ] **gRPC Python 接入**:无 HTTP header 概念;canonical 字段通过 gRPC metadata 传;`opentelemetry-instrumentation-grpc` 自动写 `trace_id` / `span_id`;业务字段需要自定义 `ServerInterceptor` 读 metadata + `bind_contextvars`

- [ ] **pytest 与 SDK 测试 fixture 冲突**:老项目可能有自家 `caplog` wrapper 或 `StreamHandler` 方案;引入 SDK `log_capture` fixture 前先跑 `grep -rlnE 'caplog|StreamHandler' tests/` 盘点冲突范围(见 [../python/execution.md §5](../python/execution.md))

---

## 不适用 python-fastapi-demo 的情况

你的项目如果满足以下任何一条,**直接套 demo 的做法可能不够**:

- **非 FastAPI(Flask / Django / aiohttp)**:middleware 挂载方式不同;Flask 用 WSGI middleware(`app.wsgi_app = ...`);Django 用 middleware class(见 §4 Django quirk);参考实现见 [../python/sdk-usage.md §8.2](../python/sdk-usage.md)
- **已有存量日志迁移**:demo 是 greenfield,无 Step 0 baseline / Step 4 PII 纠错 / Step 5 测试改写的参考;看 [../python/execution.md §2](../python/execution.md) 的"已有日志迁移"列
- **有 PII dataclass**:demo 没有带 sensitive 字段的 dataclass,IDTYPE 反射路径未覆盖;补 `field(metadata={"sensitive": "<idtype>"})` 标注 + `%s` 占位符写法,见 [../python/sdk-usage.md §3](../python/sdk-usage.md)
- **需要 OTel 接入**:demo 不装 OTel,`trace_id` / `span_id` 来自 header 手动 bind;真实生产部署通常用 `opentelemetry-instrumentation-fastapi` 自动写 span context——此时 middleware 里不要再手动 `bind_contextvars(trace_id=...)`(双写),见 [../python/sdk-usage.md §4](../python/sdk-usage.md)
- **Celery / BackgroundTasks / Cron**:无 HTTP 请求入口的任务需要手动管理 contextvars 生命周期(见 §4 quirks + [../python/sdk-usage.md §8.3](../python/sdk-usage.md))
