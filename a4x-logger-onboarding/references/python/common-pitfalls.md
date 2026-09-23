# Python 接入高频坑

已踩过的坑,按影响严重程度排序。每条给:**现象 → 根因 → 修复 → 自检 grep**。

> 读本文前建议先看 [sdk-usage.md](sdk-usage.md)——contextvars 写入方约定(§2)、IDTYPE 反射机制(§3)、OTel instrumentation(§4)在那里有完整说明;本文只讲"踩坑",不重复基础用法。

---

### 1. contextvars 在 `asyncio.create_task()` 跨 task 边界丢失

**现象**:`asyncio.create_task()` 产生的子任务里日志缺少 `user_id` / `request_id`,即使 ASGI middleware 已经 `bind_contextvars`。

**根因**:Python 3.7+ 的 `asyncio.create_task()` 会自动把**创建时刻**的 `contextvars` 拷贝到子 task——只要 task 是在请求协程链上 spawn 的,就能拿到父 context。坑在于以下两种场景会**跳出**请求 context：

- `loop.run_in_executor(None, fn)` / `ThreadPoolExecutor` 里运行的线程：线程不继承 `asyncio` task 的 contextvars 快照，拿到的是线程自己的 context（初始为空）。
- 应用启动期（`lifespan` / `on_startup`）里 `create_task` 产生的长驻后台任务：spawn 时请求 context 尚未建立，快照为空。

```python
# ❌ 错:run_in_executor 里的 fn 跑在 thread pool,拿不到请求 contextvars
async def handle(request):
    result = await loop.run_in_executor(None, blocking_fn)
    # blocking_fn 里 logger.info(...) → user_id / trace_id 不出现

# ❌ 错:应用启动时 spawn 的常驻 task,context 快照是空的
@app.on_event("startup")
async def startup():
    asyncio.create_task(background_worker())  # 此时没有请求 context
```

**修复**:

```python
import contextvars
import asyncio
from concurrent.futures import ThreadPoolExecutor

# ✅ 修复 run_in_executor:手动把当前 contextvars 快照传给线程
async def handle(request):
    ctx = contextvars.copy_context()           # 请求协程里拍快照
    result = await loop.run_in_executor(
        None, ctx.run, blocking_fn            # 在线程里用 ctx.run() 执行
    )

# ✅ 修复常驻后台 task:在请求协程里 spawn,而不是 startup 里
async def handle(request):
    asyncio.create_task(per_request_bg_job())  # 自动继承请求 context
```

对于真正需要在 `startup` 里 spawn 的全局后台 task，不依赖请求 context，在任务函数里显式 `bind_contextvars(task_name="worker")` 即可。

**自检 grep**:

```bash
# 找 run_in_executor 调用,人工确认是否在请求路径里且需要 contextvars
grep -rn 'run_in_executor' --include='*.py' src/

# 找 on_event("startup") / lifespan 里的 create_task(可能拿到空 context 快照)
grep -rn 'create_task' --include='*.py' src/ | grep -v test
```

---

### 2. uvicorn `--workers N` 多进程模式 contextvars 不跨进程传播

**现象**:单 worker 开发时日志字段正常；上生产用 `uvicorn main:app --workers 4` 后，部分请求的 `user_id` / `request_id` 不出现，且现象是随机的（因 worker 轮转）。

**根因**:`contextvars` 是进程内隔离的——每个 worker 进程有独立的内存空间，`bind_contextvars` 写入的值不会跨进程共享。这本身是正确的设计（跨请求隔离），但踩坑场景通常是：

- middleware 在父进程（master）里调用了 `bind_contextvars`（比如在模块 import 阶段或 `preload_app=True` 时的 `startup` 钩子里），worker fork 后拿到的是 fork 时的快照，之后的请求不会更新。
- `preload_app=True`（gunicorn 默认，uvicorn 通过 `--preload` 开启）时，app 在 master 进程里初始化，`setup_logging()` 的副作用（如 structlog processor 链）在 fork 前建立，子进程继承——这是正常的；但若 `setup_logging()` 之后、fork 之前做了 `bind_contextvars`，子进程会带着脏快照启动。

**修复**:

```python
# ✅ 正确:setup_logging 在入口调用(每个 worker fork 后各自初始化也没问题)
# main.py
from a4x_logger import setup_logging
setup_logging("user-service", config_file="logger.yaml")

app = FastAPI()
app.add_middleware(MyBusinessMiddleware)   # 请求到来时再 bind,进程内隔离 OK

# ❌ 错:不要在 startup / lifespan 里做全局 bind_contextvars
# (每个 worker 共享 bind,但下一个请求进来 middleware 会 unbind+rebind,
#  所以实际影响有限——但语义混乱,容易引发误解)
@app.on_event("startup")
async def startup():
    structlog.contextvars.bind_contextvars(service_mode="prod")  # 别这样做
```

关键记忆点：**bind_contextvars 只在请求 middleware / handler 里做**，进程级别的常量信息放 `setup_logging(service_name=...)` 或 `logger.yaml`，SDK 会把它们写进每条 JSON 的 `service` / `instance` 字段。

**自检 grep**:

```bash
# 找 startup / lifespan 里的 bind_contextvars(可能产生跨请求污染)
grep -rn 'bind_contextvars' --include='*.py' src/ | grep -v test
# 结合人工检查:每处 bind 是否都在请求路径(middleware / handler)里,而不是模块级 / startup
```

---

### 3. structlog 和 stdlib `logging` 双管线冲突

**现象**:同一次请求里既有带 `trace_id` / `user_id` 的 JSON 行,又有没有这些字段的纯文本行(或格式不对的 JSON)。`uvicorn` 自身的访问日志格式正常,业务代码里某些 `logging.info(...)` 调用的输出 shape 却不一样。

**根因**:`setup_logging()` 会把 stdlib root logger 桥接到 structlog pipeline,但有两种情况会让某个 logger 绕过桥接:

1. 业务代码里手动对某个 logger 做了 `addHandler()` 或 `setFormatter()`,导致该 logger 不再 propagate 到 root。
2. 框架(如 `uvicorn`)用 `--log-config` 显式指定了 logging config 文件,它的配置会在 `setup_logging()` 之后加载并覆盖 root handler 的 formatter。

```python
# ❌ 情形 1:手动 addHandler 绕过桥接
import logging

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))  # plain-text formatter
logging.getLogger("myapp.orders").addHandler(handler)
logging.getLogger("myapp.orders").propagate = False  # 彻底脱离 structlog 管线
```

**修复**:

```python
# ✅ 修复情形 1:清掉手动注册的 handler,让 propagate=True 走 root logger
import logging

order_logger = logging.getLogger("myapp.orders")
order_logger.handlers.clear()
order_logger.propagate = True   # 默认已经是 True;显式写出来防误改

# 调声音大小只用 setLevel,不要 addHandler
order_logger.setLevel(logging.WARNING)
```

```bash
# ✅ 修复情形 2:uvicorn CLI 不传 --log-config
uvicorn main:app --host 0.0.0.0 --port 8000
# 而不是:
# uvicorn main:app --log-config uvicorn-log.yaml   ← 会覆盖 SDK formatter
```

如果 uvicorn / gunicorn 的 `--log-config` 是历史遗留必须保留,在配置文件里把 formatter 指向 SDK 的 formatter 类(参考 `<sdk>/docs/architecture/python/usage.md §5.3`)。

**自检 grep**:

```bash
# 找手动 addHandler / setFormatter(危险:可能绕过 structlog 管线)
grep -rn 'addHandler\|setFormatter\|propagate\s*=\s*False' --include='*.py' src/ | grep -v test

# 找 --log-config 参数
grep -rn '\-\-log-config' Makefile docker-compose.yml .gitlab-ci.yml k8s/ 2>/dev/null || true
```

---

### 4. OTel Python instrumentation 没装全 → DB / 外部调用无 `trace_id`

**现象**:FastAPI 接口自身的日志有 `trace_id`,但在同一请求里触发的 SQLAlchemy 查询或 `httpx` 出站请求的日志里 `trace_id` 不出现,ELK 里跨服务 trace 链路断开。

**根因**:`opentelemetry-instrumentation-fastapi` 只负责为**入站 HTTP 请求**创建 span 和注入 trace context 到当前协程——FastAPI handler 里的代码确实能拿到 `trace_id`。但如果 DB 驱动或出站 HTTP 客户端没有对应的 instrumentation,那些调用运行在新 span context 里(或根本没 span),`trace_id` 不会自动传播到它们的日志里。

常见漏装:

| 需要 trace 的调用 | 需要的包 |
|---|---|
| SQLAlchemy / asyncpg | `opentelemetry-instrumentation-sqlalchemy` |
| `httpx` 出站请求 | `opentelemetry-instrumentation-httpx` |
| `requests` 出站请求 | `opentelemetry-instrumentation-requests` |
| Redis(`redis-py`) | `opentelemetry-instrumentation-redis` |
| Celery / RQ 任务 | `opentelemetry-instrumentation-celery` |

**修复**:

```bash
pip install \
  opentelemetry-instrumentation-fastapi \
  opentelemetry-instrumentation-sqlalchemy \
  opentelemetry-instrumentation-httpx \
  opentelemetry-instrumentation-requests \
  opentelemetry-sdk
```

```python
# main.py — 在 setup_logging + app 创建之后,instrument 所有客户端
from a4x_logger import setup_logging
import structlog
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

setup_logging("user-service", config_file="logger.yaml")

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)
SQLAlchemyInstrumentor().instrument(engine=engine)   # ✅ DB 调用现在有 trace_id
HTTPXClientInstrumentor().instrument()               # ✅ 出站 HTTP 也有 trace_id
```

**自检 grep**:

```bash
# 找 requirements.txt / pyproject.toml 里装了哪些 OTel instrumentation
grep -rn 'opentelemetry-instrumentation' requirements*.txt pyproject.toml 2>/dev/null

# 对照项目实际用到的库(sqlalchemy / httpx / redis 等),检查有没有漏
grep -rn 'import sqlalchemy\|import httpx\|import redis\|import celery' --include='*.py' src/ | grep -v test
```

---

### 5. f-string / `.format()` / `+` 拼接 PII 字段绕过 IDTYPE 反射

**现象**:业务 dataclass 已标 `field(metadata={"sensitive": "user_id"})`,日志里 `user_id` 仍是明文 `12345` 而不是 `[IDTYPE:user_id:12345]`。去掉 f-string 改用 `%s` 占位符后问题消失。

**根因**:SDK IDTYPE 反射只在**占位符路径**(`*args` 位置参数)触发——SDK processor 在 `structlog` 处理链里拿到原始对象,才能调 `dataclasses.fields(obj)` 读 `f.metadata.get("sensitive")`。f-string / `.format()` / `%` 手动拼接 / `str()` 拼接都在**SDK 入口之前**就把对象变成字符串,SDK 看不到原始对象,反射路径静默跳过,PII 明文落盘。详细机制见 [sdk-usage.md#3-idtype--dataclass-metadata](sdk-usage.md#3-idtype--dataclass-metadata)。

```python
from dataclasses import dataclass, field

@dataclass
class User:
    user_id: str = field(metadata={"sensitive": "user_id"})
    name: str = ""

u = User(user_id="12345", name="alice")

# ❌ f-string:SDK 入口前 u 已变字符串,IDTYPE 反射失效,PII 明文
logger.info(f"user {u} login")

# ❌ .format():同理
logger.info("user {} login".format(u))

# ❌ 手动 % 拼接:同理
logger.info("user %s login" % u)

# ❌ str 拼接:同理
logger.info("user " + str(u) + " login")

# ✅ 正确:template + 原始对象作为位置参数,SDK 自己做 %s 替换 + 反射
logger.info("user %s login", u)
# → {"message":"user {\"user_id\": \"[IDTYPE:user_id:12345]\", \"name\": \"alice\"} login"}
```

**CI 拦截**(必须):f-string 是 Python 肌肉记忆,code review 拦不住,要用 lint 规则强制:

```toml
# pyproject.toml
[tool.ruff.lint]
select = ["G"]   # flake8-logging-format
# G004: logging-fstring-interpolation   ← 拦 f-string
# G001: logging-string-format           ← 拦 .format()
# G002: logging-percent-format          ← 拦 % 手动拼接
```

**自检 grep**:

```bash
# 找 logger 调用里有 f-string 的行(高危)
grep -rn 'logger\.\w\+(f"' --include='*.py' src/ | grep -v test

# 找 logger 调用里有 .format( 的行
grep -rn 'logger\.\w\+(.*\.format(' --include='*.py' src/ | grep -v test

# 找 logger 调用里有字符串拼接 + 的行(粗筛,需人工复核)
grep -rn 'logger\.\w\+(".*"[[:space:]]*+' --include='*.py' src/ | grep -v test
```

---

### 6. 裸 `print()` 调用绕过整个日志管线

**现象**:生产日志里出现非 JSON 格式行,ELK parse 失败报 `_grokparsefailure`。或者某些业务操作在日志平台完全看不到痕迹,只有本地 stdout 有输出。

**根因**:`print()` 直接写 stdout,完全绕过 structlog / SDK pipeline:没有 canonical 字段(`trace_id` / `user_id` 等)、没有 IDTYPE 脱敏、没有 OTel 关联、没有 `timestamp` / `level` / `service` 字段。ELK Logstash 的 JSON filter 拿到 plain-text 行后 parse 失败,日志会被丢进 `_grokparsefailure` 索引或直接丢弃。

```python
# ❌ 全部绕过管线:
print(f"user {user_id} logged in")        # 无 trace_id / 无 IDTYPE / PII 明文
print("DEBUG:", order)                    # ELK parse 失败
```

**修复**:

```python
import structlog

logger = structlog.get_logger()

# ✅ 全部走 SDK 管线:
logger.info("user %s logged in", user_id)   # 有 trace_id / 有 IDTYPE / JSON 格式
logger.debug("order %s", order)             # 级别可控,结构化
```

**lint 拦截**:ruff `T201` 规则检测 `print()` 调用:

```toml
# pyproject.toml
[tool.ruff.lint]
select = ["T201"]   # flake8-print — 检测 print() 调用
```

如有合法的 `print()` 用途(CLI 工具输出等),用 `# noqa: T201` 显式豁免,让 grep / CI 能区分"遗忘的 print"和"有意为之的 print"。

**自检 grep**:

```bash
# 找非注释的 print( 调用(排除 test 和 noqa 行)
grep -rn '^\s*print(' --include='*.py' src/ | grep -v test | grep -v '# noqa'

# 统计数量:0 为目标
grep -rc '^\s*print(' --include='*.py' src/ | grep -v ':0' | grep -v test
```

---

## 速查表

接入快 release 前过一遍:

- [ ] `asyncio.create_task()` 产生的子 task 在请求协程链上 spawn,不在 startup 里 spawn(坑 1)
- [ ] `run_in_executor` / ThreadPool 调用处用 `ctx = contextvars.copy_context(); ctx.run(fn)` 传入快照(坑 1)
- [ ] `bind_contextvars` 只在请求 middleware / handler 里调用,不在 startup / 模块级调(坑 2)
- [ ] 没有手动 `addHandler` / `setFormatter` / `propagate=False` 绕过 structlog 桥接(坑 3)
- [ ] uvicorn / gunicorn 没有 `--log-config` 覆盖 SDK formatter(坑 3)
- [ ] `requirements.txt` 里有项目实际用到的所有 OTel instrumentation 包(坑 4)
- [ ] 所有 PII 字段用 `%s` 占位符传原始对象,ruff `G` 规则在 CI 开启(坑 5)
- [ ] 无遗忘的裸 `print()`,ruff `T201` 规则在 CI 开启(坑 6)
