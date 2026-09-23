# Execution — Python 接入流程纪律

> 本文件教 **agent 怎么按流程接入 Python 服务 + 怎么保证业务无回归**。Phase 0 Recon 通过后走本文件。
>
> 配套文件:
> - SDK API 用法 → [sdk-usage.md](sdk-usage.md)
> - 项目探查 → [../project-recon.md](../project-recon.md)
> - 新写日志规约 → [../log-style-guide.md](../log-style-guide.md)(soft,migration 不强制)
> - 通用坑 → [common-pitfalls.md](common-pitfalls.md)
> - PII 扫描 → [pii-scanner.md](pii-scanner.md)
>
> **基准版本**:`a4x-logger` 1.0.0 / Python 3.9+ / structlog 25.x

---

## §1 7 步骨架

Python SDK `setup_logging` 一行接管全部 logging 管线,**无需 Go 的 11 步全量**。按下表执行,
**每行右侧适用性看 §2 表,机械跑全 7 步可能做无用功**。

| # | 步骤 | 变动量 | 可独立上线 | 备注 |
|---|---|---|---|---|
| 0 | **Pre-flight baseline** — `pytest -x --tb=short` 记 failing tests + 生成 smoke curl 脚本 | 1 个脚本 | n/a | [详](#0-baseline) |
| 1 | **pip install + `setup_logging`**(GIT_CONFIG insteadOf 拉私服 + `setup_logging` 入 main 入口) | `requirements.txt` / `pyproject.toml` + main 文件 | ✅ | [详](#1-install-setup-logging) |
| 2 | **写业务 ASGI/WSGI middleware**(`bind_contextvars` 写入 canonical 字段) | 1-2 个 middleware 文件 | ✅ | [详](#2-middleware) |
| 3 | **dataclass 加 `metadata={"sensitive": ...}` PII 标注** | M 个 dataclass | ✅ 静态改 | [详](#3-pii-metadata) |
| 4 | **存量 logging 调用 PII 写法纠错**(f-string / `.format()` / `+` 拼接 → `%s` 占位符) | 业务代码,可分批 | ✅ | [详](#4-pii-fix) |
| 5 | **测试改写**(`caplog` → SDK `log_capture` fixture / `LogCapture`) | test 文件 | ✅ | [详](#5-test) |
| 6 | **CI/Docker pip 私服鉴权**(GIT_CONFIG insteadOf + Dockerfile 双 stage) | `.gitlab-ci.yml` + `Dockerfile` | ✅ | [详](#6-ci) |
| 7 | **写设计文档 / 交付总结** | 1 份 md | ✅ | [详](#7-doc) |

---

## §2 按模式查 Step 适用性

Recon 探到的"项目现状"决定每步的实际工作量。**Agent 不要机械跑全 7 步**。

| Step | 全新接入 | 已有日志迁移 |
|---|---|---|
| 0 baseline | ⚠️ 只跑 `pytest` 记失败数;**跳过 smoke discover**(项目没业务 endpoint) | ✅ 完整(test + smoke curl) |
| 1 install + setup_logging | ✅ | ✅ |
| 2 middleware | ✅ 直接挂参考实现 middleware | ⚠️ 若已有 `AuthMiddleware` / `JwtMiddleware` 在 `bind_contextvars`,在自家改 + 加 `unbind_contextvars` 到 `finally`,不挂参考实现(避免双写) |
| 3 dataclass metadata | ✅(新 dataclass 直接打) | ✅ |
| 4 PII 纠错 | ⚠️ 全新接入只在 demo/example 代码里纠错 | ✅ **主战场** |
| 5 测试改写 | ⚠️ 全新接入可后做 | ✅ |
| 6 CI/Docker | ✅ | ✅ |
| 7 文档 | ⚠️ 全新接入写 setup doc(短) | ✅ 写 migration doc |

**符号**:✅ 必做 / ⚠️ 视情况调整 / ➖ 跳过。

---

## §3 Step 0-1 详细

### Step 0:Pre-flight baseline {#0-baseline}

**目的**:建立回归基准,接入后能 diff。

```bash
# 1. 记现有测试失败数(baseline)
pytest -x --tb=short 2>&1 | tee /tmp/baseline-test.log
grep -E 'passed|failed|error' /tmp/baseline-test.log

# 2. 已有服务:生成 smoke curl 脚本
# 全新接入:跳过 smoke discover(没有业务 endpoint 可 smoke)
```

具体 smoke 脚本生成方法见 [../regression-smoke-template.md](../regression-smoke-template.md)。
记录失败数量到 `/tmp/baseline-test.log`。后续每步的 7 项验证都要对比这个数字。

### Step 1 详细:pip install + setup_logging {#1-install-setup-logging}

#### 1a. 安装 SDK

**过渡期**(Nexus 3.x 上线前):通过 GitLab Job Token + `GIT_CONFIG_*` insteadOf 模式直接拉。

**`requirements.txt`** 加一行(干净 URL,不带 token):

```
a4x-logger @ git+https://gitlab.addx.ai/CLOUD/a4x-logger-sdk.git@python/v1.0.0#subdirectory=python
```

**开发者本机**:

```bash
pip install 'git+ssh://git@gitlab.addx.ai/CLOUD/a4x-logger-sdk.git@python/v1.0.0#subdirectory=python'
```

或配一次 git config insteadOf 后直接 `pip install -r requirements.txt`:

```bash
git config --global url."git@gitlab.addx.ai:".insteadOf "https://gitlab.addx.ai/"
pip install -r requirements.txt
```

> **CI 鉴权**:`GIT_CONFIG_*` 三个变量见 §6 / [sdk-usage.md §1](sdk-usage.md#1-5-分钟接入)。
> Docker build 场景(pip 在 Dockerfile build stage 里运行)的完整 `ARG CI_JOB_TOKEN` + multi-stage 方式见 §6。

#### 1b. `setup_logging` 入 main

在应用入口(**第一条 `logger.*()` 调用 / `app = FastAPI()` 之前**)调一次 `setup_logging()`:

```python
# main.py(应用入口最顶部)
from a4x_logger import setup_logging

setup_logging("user-service", config_file="logger.yaml")

# 之后才 import FastAPI / Flask / uvicorn 等
from fastapi import FastAPI
app = FastAPI()
```

> **`setup_logging` 绝不 raise**(ADR-15 panic safety)。配置文件加载失败时降级到默认值 + stderr WARN。

**签名快查**([sdk-usage.md §1](sdk-usage.md#1-5-分钟接入)):

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

配置优先级:**programmatic args > `config_file` > 默认值**。

#### 1c. logger.yaml — 参考模板

```yaml
a4x-logger:
  service-name: ${SERVICE_NAME:my-service}  # 优先读 env,无 env 时显式配
  level: INFO                                # DEBUG | INFO | WARNING | ERROR
  max-message-length: 32768                 # 默认 32768(32KB),最小 256

  # Key-triggered 脱敏(ADR-14)
  # SDK 不内置任何规则,项目要哪些必须显式配
  sensitive:
    enabled: true
    on-error: log_raw                        # log_raw | redact | drop
    masks:
      phone:
        - "\\b1[3-9]\\d{9}\\b"
      email:
        - "\\b[\\w.+-]+@[\\w-]+\\.[\\w.]+\\b"
      api-token:
        - "(sk-[a-zA-Z0-9]{32})"

  # 旧项目迁移期:canonical key 不在 contextvars 时走 alias 回退
  context:
    aliases:
      firmware_version:
        - firmwareId
```

> **mask rule 扩展原则**:新增 PII 字段时,对照 [../fields-and-idtype.md](../fields-and-idtype.md)
> IDTYPE 变体表,逐字段写 key-triggered 规则。

---

## §4 Step 2 详细 — ASGI/WSGI Middleware {#2-middleware}

### 核心立场

**SDK 不提供成品 middleware**,业务必须自己写。SDK 是 **contextvars 的只读消费者**——每条日志输出 JSON 时,SDK 从当前协程/线程的 contextvars 读取 12 个 canonical 字段写入 JSON。**SDK 不会自己往 contextvars 里写任何业务字段**。

### 决策树

```
Q: 项目已有 AuthMiddleware / JwtMiddleware 在 bind_contextvars(user_id=...) 等 canonical 字段?
├── 是 → 在自家 middleware 里加其余 bind_contextvars 调用(request_id / device_msg_src 等),
│         不挂参考实现(避免双写覆盖)
│         只需:
│         1. 确认 bind key 是 canonical 名(见下表),或在 alias_mapping 配了映射
│         2. 把 unbind_contextvars(*CANONICAL_KEYS) 移到 finally 块(协程复用安全)
└── 否 → 直接挂参考实现 middleware
          全新接入:user_id 块先留 TODO,等 auth 接入后解开
```

**canonical contextvars key 速查**(12 个,详见 [sdk-usage.md §2](sdk-usage.md#2-contextvars-写入方约定)):

| 分类 | key |
|---|---|
| Tracing | `trace_id` / `span_id`(OTel 自动,勿手动) / `request_id` |
| Context | `user_id` / `tenant_id` / `account_id` / `serial_number` / `model_no` / `firmware_version` / `device_msg_src` / `session_id` / `task_name` |

### 协程复用事故场景

> **类比 Java MDC 事故**:Python asyncio worker 线程可能被多个请求复用。Middleware `try` 块结束后如果没有 `finally` 里的 `unbind_contextvars`,下一个请求会读到上一个请求残留的 `user_id` / `trace_id`。
>
> **教训:`unbind_contextvars(*CANONICAL_KEYS)` 必须放 `finally`,不是 `try` 末尾。**

```python
# ✅ 正确:finally 保证任何退出路径都 unbind
structlog.contextvars.unbind_contextvars(*CANONICAL_KEYS)
structlog.contextvars.bind_contextvars(**bindings)
try:
    await self.app(scope, receive, send)
finally:
    structlog.contextvars.unbind_contextvars(*CANONICAL_KEYS)  # 不可省
```

### 同字段单写入方红线

同一个 canonical 字段**只能有一个写入方**。若自家 AuthMiddleware 已写 `user_id`,不要再让参考实现也写——会产生双写覆盖。

### 写法参考

三种具体写法的完整代码见 [sdk-usage.md §8](sdk-usage.md#8-asgiwsgi-middleware-参考实现):

- **写法 A**:项目已有 AuthMiddleware / JwtMiddleware(最常见)
- **写法 B**:专用 LoggingContextMiddleware,网关注 header,服务内做 header → contextvars 映射
- **写法 C**:完全不写 middleware,仅依赖 OTel 自动写 `trace_id` / `span_id`(纯查询服务 / 批处理)

---

## §5 Step 3-4 详细 — dataclass metadata + PII 纠错

### Step 3:dataclass `metadata={"sensitive": ...}` PII 标注 {#3-pii-metadata}

#### 3a. 发现命令

```bash
# 找 PII 字段名的 dataclass(对照 fields-and-idtype.md 变体表)
grep -rnE '\b(user_id|userId|email|phone|mac|mac_addr|serial_no|serial_number|ticket_id|user_sn|device_sn|device_mac)\b' \
  --include='*.py' <项目路径>
```

对照 [../fields-and-idtype.md](../fields-and-idtype.md) IDTYPE 变体表,逐字段加
`field(metadata={"sensitive": "<idtype>"})` 标注(具体 idtype 字符串见 [sdk-usage.md §3](sdk-usage.md#3-idtype--dataclass-metadata))。

#### 3b. dataclass 标注写法

```python
from dataclasses import dataclass, field

@dataclass
class User:
    user_id: str = field(metadata={"sensitive": "user_id"})
    email: str = field(metadata={"sensitive": "email"})
    phone: str = field(metadata={"sensitive": "phone"})
    name: str = ""      # 无标记,原样输出(除非命中 key-triggered 规则)
```

metadata key 必须是字符串 `"sensitive"`,value 是 IDTYPE 类型名(小写,与 `spec/sensitive_types.json` 对齐)。

#### 3c. IDTYPE 反射闭环条件

IDTYPE 反射**只在占位符路径**(`*args` 位置参数)生效——**必须同时满足**:

1. dataclass 字段有 `metadata={"sensitive": "<idtype>"}`
2. logger 调用用 `%s` 占位符 + 传原始对象:`logger.info("user %s", u)`

两个条件缺一不可。标注了但用 f-string 传仍然无效。

### Step 4:PII 写法纠错 — 机械替换 4 规则 {#4-pii-fix}

**核心立场**:migration 不是重构。目标是把 PII 送进 SDK 管线,**不是顺手规约化所有历史日志**。

#### 4 条规则

| 规则 | 含义 |
|---|---|
| **① API 必换** | `print(...)` / `traceback.print_exc()` → `logger.info(...)` / `logger.exception(...)` |
| **② message 文本基本保留** | **不发明 action 名,不强加 `result=`,不重命名变量**。原 message 啥样,换完基本啥样 |
| **③ inline 动态值套 `%s`** | `logger.info("user " + str(u))` → `logger.info("user %s", u)`,让 SDK 拿到原始对象 |
| **④ SDK 硬要求** | (a) `except` 块里用 `logger.exception("msg")` 自动捕 traceback;<br>(b) 显式带异常:`logger.error("msg %s", x, exc_info=e)` |

#### 发现命令

```bash
# 扫 f-string / .format / % 手动拼接打 PII 的写法
grep -rnE 'logger\.\w+\(f"|logger\.\w+\(.*\.format\(|logger\.\w+\(.*%\s' \
  --include='*.py' src/

# 扫 str 拼接写法
grep -rnE 'logger\.\w+\(.*\+\s*(str|f"|\w)' \
  --include='*.py' src/

# 扫裸 print / traceback.print_exc
grep -rnE '\bprint\(|\btraceback\.print_exc\(' \
  --include='*.py' src/
```

#### 典型替换示例

```python
# ❌ f-string: SDK 入口前 u 已变成字符串,IDTYPE 反射失效,PII 明文落盘
logger.info(f"user {u} login from {ip}")

# ❌ .format(): SDK 拿不到原始对象
logger.info("user {} login".format(u))

# ❌ % 手动拼接: 同理
logger.info("user %s login" % u)

# ❌ str 拼接: 同理
logger.info("user " + str(u))

# ✅ 位置参数 + %s 占位符 → SDK 拿到原始对象,metadata 反射生效
logger.info("user %s login from %s", u, ip)
```

```python
# ❌ 吞 exception,traceback 丢失
try:
    process_order(order)
except Exception as e:
    logger.error("failed")

# ✅ logger.exception() 自动捕获当前 traceback → exception 字段
try:
    process_order(order)
except Exception:
    logger.exception("订单处理失败 order_id=%s", order.id)

# ✅ 或显式传 exc_info
try:
    process_order(order)
except Exception as e:
    logger.error("订单处理失败 order_id=%s", order.id, exc_info=e)
```

```python
# ❌ print / traceback.print_exc
print("starting")
traceback.print_exc()

# ✅ stdlib logging / structlog
import structlog
logger = structlog.get_logger()
logger.info("starting")
logger.exception("unexpected error")   # 在 except 块里
```

#### 不该做的

- ❌ 不要给每条 `logger.info("user " + id)` 强行起 `action=user_found` — 命名决策等于重写业务
- ❌ 不要把 `uid` 变量重命名成 `user_id` — 那是变量重构,不是日志迁移
- ❌ migration 时不套 [../log-style-guide.md](../log-style-guide.md) 的 `action=/result=` convention — 那是**新写日志推荐**

#### 分批策略

| 规模 | 建议 |
|---|---|
| < 20 个调用点 | 1-2 个 commit 搞定 |
| 20-100 个调用点 | 5-10 批,按 module / package 分 |
| > 100 个调用点 | 按 module 分;单 module > 30 个调用点再按子包细分 |

---

## §6 Step 5-7 详细

### Step 5:测试改写 {#5-test}

#### 5a. 发现旧的捕获写法

```bash
# 找靠 caplog / StreamHandler / bytes buffer 做日志捕获的测试
grep -rlnE 'caplog|StreamHandler|bytes\.BytesIO.*log|logging\.handlers' \
  --include='*.py' tests/
```

#### 5b. 替换为 SDK `log_capture` fixture

`log_capture` 是 pytest fixture,通过 `conftest.py` 注册后在测试函数参数列表直接声明,**不需要 import**。完整 API 见 [sdk-usage.md §6](sdk-usage.md#6-测试捕获)。

```python
# conftest.py(项目测试根目录)
from a4x_logger.testing.log_capture import log_capture  # noqa: F401
```

```python
# test_example.py
import structlog
from a4x_logger import setup_logging

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

```python
# test IDTYPE 反射
from dataclasses import dataclass, field
from a4x_logger.testing.canonical_fields import assert_log_emit

@dataclass
class User:
    user_id: str = field(metadata={"sensitive": "user_id"})
    name: str = ""

def test_idtype_reflection(log_capture):
    setup_logging("test-service")
    u = User(user_id="12345", name="alice")
    structlog.get_logger().info("user %s", u)

    line = log_capture.last()
    assert "[IDTYPE:user_id:12345]" in line["message"]
    assert "alice" in line["message"]           # 无标记字段原样出现

    # 或用断言工具
    assert_log_emit(line, {
        "level": "INFO",
        "tenant_id": {"absent": True},           # sentinel:断言此字段不出现
    })
```

**`log_capture` fixture 自动清空 contextvars**(before / after),防止跨测试泄漏。每个测试函数都需要调一次 `setup_logging()` 重新配置 SDK。

### Step 6:CI/Docker {#6-ci}

#### 6a. `.gitlab-ci.yml` — GIT_CONFIG insteadOf 鉴权

在全局 `variables:` 块加三个变量(基于 SDK commit `ac4cbde` 模式):

```yaml
variables:
  GIT_CONFIG_COUNT: "1"
  GIT_CONFIG_KEY_0: "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf"
  GIT_CONFIG_VALUE_0: "https://gitlab.addx.ai/"
```

GitLab Runner 启动 job 时自动注入 `${CI_JOB_TOKEN}`,**业务方无需维护任何 secret**。

完整 `.gitlab-ci.yml` 模板见 [`ci-docker-template.md`](ci-docker-template.md)。

#### 6b. Dockerfile 双 stage

```dockerfile
# build stage:仅在 build 阶段配 git config + pip install
FROM python:3.11 AS builder
ARG CI_JOB_TOKEN=""

# 仅在 build stage 配 git config insteadOf;不写到任何持久化文件,避免泄漏到镜像层
RUN if [ -n "$CI_JOB_TOKEN" ]; then \
      git config --global \
        "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf" \
        "https://gitlab.addx.ai/"; \
    fi

COPY requirements.txt .
RUN pip install --target=/install -r requirements.txt

# runtime stage:从 builder 拷贝装好的 site-packages,不带任何 git/token 痕迹
FROM python:3.11-slim
COPY --from=builder /install /usr/local/lib/python3.11/site-packages
COPY . /app
WORKDIR /app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> **关键安全约束**:`ARG CI_JOB_TOKEN` 和 `git config insteadOf` **只能出现在 build stage**,runtime stage 不能引用——否则 token 会泄漏到最终镜像层。

传 token 给 docker build:

```yaml
# .gitlab-ci.yml build job
build-image:
  script:
    - docker build --build-arg "CI_JOB_TOKEN=${CI_JOB_TOKEN}" -t myapp .
```

### Step 7:文档 {#7-doc}

#### 全新接入 → Setup Doc

路径:`docs/architecture/logging/a4x-logger-setup.md`

```markdown
# A4X Logger SDK 接入设计 — <service-name>

| 文档状态 | YYYY-MM-DD |
| 对应 MR | !<number> |
| SDK 版本 | a4x-logger 1.0.0 |

## 1. 接入目标
- 全新服务 day 1 用统一日志 SDK
- 5 点能力:结构化 JSON / PII mask / canonical 字段 / IDTYPE 反射 / trace 关联

## 2. 实装范围
  2.1 pip install + `setup_logging("my-svc", config_file="logger.yaml")`
  2.2 ASGI/WSGI middleware:<MyBusinessMiddleware 路径>
  2.3 dataclass metadata 标注:<X 个 dataclass>

## 3. 字段契约
  3.1 Canonical 字段:request_id(✅ 已注入);user_id(⚠️ 待 auth 接入后补)
  3.2 IDTYPE tag:仅 demo dataclass 已示范
  3.3 PII mask:预置 phone / email 规则(logger.yaml)

## 4. 待办
  4.1 接入 auth 后:middleware 里 user_id 注入解开 TODO
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
| SDK 版本 | a4x-logger 1.0.0 |
| 作用范围 | `<directory>/` |

## 1. 背景与目标
  1.1 现状:日志实现(<原库:stdlib logging / structlog / loguru 直接调用>) +
      ~N 个调用点 + 当前 PII 风险
  1.2 目标:5 点能力(结构化 JSON / PII mask / canonical 字段 / IDTYPE 反射 / trace 关联)
  1.3 Non-goals:明确不做什么(避免 scope creep)

## 2. 迁移范围
  2.1 代码改动(文件级)
  2.2 配置改动(logger.yaml / CI / Dockerfile)
  2.3 测试改动

## 3. 字段契约
  3.1 Canonical 字段(middleware bind_contextvars 写入方)
  3.2 IDTYPE 反射标签(dataclass metadata)
  3.3 PII mask(logger.yaml)

## 4. 回滚策略
  4.1 完全回滚(git revert MR)
  4.2 降级:关闭 `sensitive.enabled: false`
  4.3 快速关闭:注释 `setup_logging()` 调用,恢复原日志库

## 5. 可追溯链接
  - MR / SDK repo / skill 链接
```

在 `docs/architecture/overview.md` 或 README 加一行 link 指向本文档。

---

## §7 每批 7 项验证 + 合并前 5 项 gate

### 每批 7 项强制验证

**Agent 自己跑,不 delegate**。改完一批 → 跑 7 项全过 → commit → 下一批。
任何一项 fail,**STOP 修或 revert,不累积**。

| # | 动作 | 命令 |
|---|---|---|
| 1 | **API 清零** — 本批 `print` / `traceback.print_exc` / 裸 f-string PII 全替换 | `grep -rE '\bprint\(|\btraceback\.print_exc\(' --include='*.py' <batch-dir>` 应 **0 命中** |
| 2 | **pytest 过** | `pytest -x --tb=short <batch-dir>` 失败数 ≤ Step 0 baseline |
| 3 | **lint 过** | `ruff check --select G <batch-dir>` 应 0 命中(若项目用 ruff) |
| 4 | **PII 扫描清零** | 跑 [pii-scanner.md](pii-scanner.md),本批扫描 0 命中才过 |
| 5 | **smoke 回归**:本批 domain endpoint diff baseline 无差异 | `./scripts/smoke-curl.sh --domain <batch> > /tmp/after.json` → diff baseline |
| 6 | **commit message 含 step 标号** | 例:`feat(<module>): migrate PII log calls to %s placeholder (step 4.1/3)`;**别用 `chore:` 偷懒** |
| 7 | **调用方同步** — 改了 dataclass 字段 → 所有 caller 都跟着改,import 过 | `grep -rn 'from <module> import <Class>\|<Class>(' --include='*.py'` 确认无 import error |

**如果 #1 失败**:本批还有漏改的 → grep 找出来全换完。
**如果 #2 失败**:dataclass signature 改动碰到测试 fixture → 修 fixture / 补默认值。
**如果 #3 失败**:残留 f-string / `.format()` / `%` 手动拼接 → 替换成 `%s` 占位符形式。
**如果 #5 失败**:不要硬推下一批。常见原因:手滑改了非日志代码 / 误改了业务逻辑。

### 合并前 5 项 gate

**5 项全过才发 MR**(已有日志迁移):

| # | gate |
|---|---|
| ① | smoke diff baseline 零差异 |
| ② | `pytest` 失败数 ≤ Step 0 baseline |
| ③ | **全仓非 SDK 日志 API 清零**:`grep -rE '\bprint\(|\btraceback\.print_exc\(|logging\.info\b|logging\.error\b|logging\.warning\b' --include='*.py' src/` 业务直接用 `logging.*` 调用应 0(排除 conftest / test_ 文件) |
| ④ | **canonical 字段无双写入**:同一个 canonical key 只有一个写入方(grep middleware 文件确认) |
| ⑤ | **dataclass `metadata={"sensitive": ...}` 反射闭环**:用 `log_capture` 抽样查 IDTYPE 渲染 — `assert "[IDTYPE:" in log_capture.last()["message"]` 至少命中 1 次 |

**全新接入 gate**(简化 — 无 smoke baseline):
- `pytest` 失败数 ≤ Step 0 baseline
- demo 新增 test 全绿(`log_capture` 能验证日志格式生成)
- 可选:起服务 `curl` 一下 endpoint 肉眼对照 [../log-style-guide.md](../log-style-guide.md) pattern

---

## §8 MR 描述模板 / 交付总结模板

### MR 描述模板(已有日志迁移用)

```markdown
## Summary
- 迁移 <service> 的 <N> 处日志调用到 a4x-logger 1.0.0
- 加 ASGI/WSGI middleware(<MiddlewareName>)注入 <fields> canonical 字段
- dataclass `metadata={"sensitive": ...}` 覆盖 <M> 个 PII 字段
- CI/Docker 配 GIT_CONFIG insteadOf pip 私服鉴权

## Baseline 数字(pytest/grep 实测)
- Pre-integration test failures: <N>
- print / traceback.print_exc 调用总数: <count>
- 分批: <B> 批 commit(step 4.1 / 4.2 / ...)

## Final 数字
- Post-integration test failures: <N>(未劣化)
- 全仓 `print(\|traceback.print_exc` 命中: 0
- PII f-string / .format / 拼接写法命中: 0
- Smoke diff baseline: 0 diff
- IDTYPE 反射闭环: `[IDTYPE:` 命中 ✓

## 合并前 5 项 gate
- ✅ ① smoke diff = 0
- ✅ ② test ≤ baseline
- ✅ ③ 全仓非 SDK 日志 API 清零
- ✅ ④ canonical 字段无双写入
- ✅ ⑤ dataclass metadata 反射闭环

## 相关
- 迁移设计文档: docs/architecture/logging/a4x-logger-migration.md
- SDK version: a4x-logger 1.0.0
```

### 典型 commit 序列

```
1. test(smoke): add regression smoke baseline                        (Step 0)
2. feat(config): pip install a4x-logger + setup_logging + logger.yaml (Step 1)
3. feat(middleware): add MyBusinessMiddleware injecting canonical fields (Step 2)
4. feat(types): add dataclass metadata sensitive PII fields           (Step 3)
5. feat(<module>): migrate PII log calls to %s placeholder (step 4.1/3) (Step 4 batch 1)
6. feat(<module>): migrate PII log calls (step 4.2/3)                (Step 4 batch 2)
7. test: replace caplog with SDK log_capture fixture                  (Step 5)
8. ci(docker): add GIT_CONFIG insteadOf auth for private pip          (Step 6)
9. docs(architecture): add a4x-logger migration design                (Step 7)
```

每个 commit 独立 review 友好 + 可独立 revert。

### 交付总结模板(agent 通过 text 输出给用户)

Step 7 结束时 agent 在对话里给用户这段 structured summary。占位符替换成真实值,
**数字 pytest/grep 实测,不编**。

```markdown
## a4x-logger 接入完成 — <service-name>

### 我做了

| 能力 | 产出物 |
|---|---|
| SDK 依赖 | `requirements.txt`:a4x-logger 1.0.0 |
| 配置文件 | `logger.yaml`(mask rules 已预置 phone/email) |
| setup_logging | `main.py` L<N>:`setup_logging("my-svc", config_file="logger.yaml")` |
| ASGI/WSGI middleware | `<middleware-path>/<MiddlewareName>.py`(request_id / user_id / ... 注入) |
| dataclass 标注 | <M> 个 dataclass 加了 `metadata={"sensitive": ...}` |
| PII 纠错 | <N> 处 f-string / .format / 拼接 → `%s` 占位符 |
| 测试改写 | <K> 个测试换用 SDK `log_capture` fixture |
| CI/Docker | `GIT_CONFIG_*` variables + `Dockerfile` multi-stage |
| 设计文档 | `docs/architecture/logging/a4x-logger-<setup/migration>.md` |

### 未做项 / TODO(视项目情况)

| 项 | 为什么没做 | 后续怎么补 |
|---|---|---|
| CI 鉴权配置 | 项目当前无 `.gitlab-ci.yml` | CI 立项时按 [ci-docker-template.md](ci-docker-template.md) 加 |
| `user_id` 字段注入 | 项目当前无 auth(JWT/session) | 接入 auth 后解开 middleware 里的 TODO 注释 |
| Smoke 回归脚本 | 项目暂无业务 endpoint | 业务接口稳定后按 [../regression-smoke-template.md](../regression-smoke-template.md) 补 |

### Baseline 数字(实测)

| 指标 | 值 |
|---|---|
| Test failures (pre-integration) | <N> |
| Test failures (post-integration) | <N>(未劣化) |
| dataclass 标注 | <M> 个 PII 字段 |
| PII 写法纠错 | <N> 处 |

### MR
<MR link>
```
