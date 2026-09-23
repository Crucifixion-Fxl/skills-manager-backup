# Project Recon

> 本文件教 **agent 怎么用 grep / Read / Glob 探查任意项目**,不假设任何标准布局。Recon 的输出是 1-shot 确认报告,用户 yes/no/修正后才进 Phase 1。

**核心原则**:
- **永远 recon 实际路径**,从不假设 `internal/logic/*Logic.go` / `internal/handler/routes.go` 这种标准 go-zero 命名
- **7 维度 + 任意日志库检测**,覆盖 greenfield / migration(任何源)/ 自研 wrapper / 混合框架
- **探不到的字段如实留空**,不瞎猜;在 1-shot 报告里明确"这项需要你填"

> **命令工具约定**:下文用 `Grep` 指 Claude Code 内置的 Grep 工具(支持 `--type=<lang>` filter)或本机 ripgrep `rg`。**如果环境只有 GNU `grep`**,把 `--type=<lang>` 替换为 `--include='*.<ext>'`(如 `--type=go` → `--include='*.go'`、`--type=java` → `--include='*.java'`、`--type=py` → `--include='*.py'`)。其它选项(`-c`/`-n`/`-r`/`-E`)两者通用。

---

## 1. 七个探查维度

| 维度 | 探查命令(通用,不假设布局) | 判据 |
|---|---|---|
| **语言 / 框架** | `Read go.mod` / `pom.xml` / `requirements.txt` / `Gemfile` / `package.json` + `Glob '**/*.api'`(go-zero 标志)| module / framework 依赖决定 |
| **项目现状** | 见 §2 多库扫描 | 决定走 greenfield 还是 has-existing |
| **CI 类型** | `Glob '.gitlab-ci.yml' / '.github/workflows/*.yml' / '.buildkite/*' / 'Jenkinsfile' / 'azure-pipelines.yml'` | 命中哪个走哪种 |
| **本地起服务** | `Read docker-compose*.yml` / `Makefile` / `README.md` / `cmd/*/main.go`(flag / env 解析提示) | 起 baseline 用 |
| **Middleware 栈** | 见 §3 多种命名覆盖 | 决定 LoggerMiddleware 挂哪里 + 哪些是前置 mw |
| **Auth 机制** | `Grep 'jwt\.\|Bearer \|X-User-Id\|session\|cookie\|authctx\|UserIDFromCtx' --type=go` | 决定 LoggerMiddleware user_id 怎么取,是否要 comment out |
| **SDK 现状版本** | `Grep 'a4x-logger-sdk' go.mod pom.xml pyproject.toml requirements.txt` | 0 = 全新接入;有版本 = 升级 / 扩展场景 |

---

## 2. 现有日志调用检测(任意库)

**不要只扫 logx**。扫描所有可能的日志库,看哪个有大量命中决定"源"是什么:

```bash
# Go 项目
Grep -c 'logx\.(Info|Error|Warn|Debug)|logc\.' --type=go          # go-zero logx
Grep -c 'log\.(Printf|Println|Print|Fatal|Fatalf|Panic)' --type=go # stdlib log
Grep -c 'logger\.(Info|Error|Warn|Debug|Infof|Errorf)' --type=go   # 通用 logger
Grep -c 'slog\.(Info|Error|Warn|Debug)' --type=go                  # Go 1.21+ slog
Grep -c 'zap\.(L\b|S\b|Logger)' --type=go                          # 裸 zap
Grep -rE 'import.*"(log|log/slog|go.uber.org/zap|github.com/sirupsen/logrus|github.com/rs/zerolog)"' --include='*.go' . | wc -l

# Java 项目
Grep -c 'LoggerFactory\.getLogger|@Slf4j' --type=java              # SLF4J / Logback(主流)
Grep -c 'log\.(info|error|warn|debug|trace)' --type=java           # 通用 SLF4J 调用点
Grep -c 'System\.out\.|\.printStackTrace\(\)' --type=java          # 裸 System.out / printStackTrace(反模式)
Grep -c 'log4j\.\|Log4j\|LogManager\.getLogger' --type=java        # 裸 log4j 直接调用
Grep -c 'String\.format.*log\|log.*\+\s*\w' --type=java            # 字符串拼接打日志(PII 风险)

# Python 项目
Grep -c 'logging\.basicConfig\|logging\.getLogger\|logging\.(info|error|warning|debug)' --type=py  # stdlib logging
Grep -c 'structlog\.get_logger\|structlog\.getLogger' --type=py    # structlog 直调(未经 setup_logging)
Grep -c 'logger\.(info|error|warning|debug|exception)' --type=py   # 通用 logger 调用点
Grep -c 'print(' --type=py                                          # 裸 print(反模式)
Grep -c 'f".*{.*}\|\.format(.*)\|.*%.*%' --type=py                 # f-string / .format / % 拼接(PII 风险)
```

### 项目现状判定(二分,**不分源库**)

| 判定 | 信号 | 走哪条流程(见 [execution.md](execution.md))|
|---|---|---|
| **全新接入**(greenfield)| 所有日志库总命中 < 10 + Logic 文件 0/极少 | Step 4/5/7 跳过,demo 在 Step 10 |
| **已有日志**(has-existing)| 任意日志库总命中 > 10 | Step 5 走机械替换 4 规则(任意源统一 recipe)|
| **混合**(有 Logic 但没日志调用)| 罕见 | 当 greenfield 处理 |

**为什么不按源库分类接入流程**:Step 5 机械替换 4 规则对任何源都一样(API 必换 / message 保留 / 动态值可套 key=%v / Fatal 拆)。skill 不需要为 "stdlib log" vs "logx" 写分叉 playbook。

---

## 3. 找 main / routes / types / middleware 的通用方法

### 3.1 Main 入口

| 探查 | 命令 |
|---|---|
| Go | `Glob 'cmd/**/main.go' 'main.go'` |
| Java | `Glob 'src/main/java/**/Application.java' / '**/*Application.java'` |
| Python | `Glob '**/main.py' '**/app.py' '**/asgi.py' '**/wsgi.py'` |

### 3.2 Routes 文件(go-zero 项目命名可能非标)

```bash
# 标准 go-zero
Glob 'internal/handler/routes.go'

# 非标
Grep 'rest\.WithMiddlewares\|server\.AddRoutes\|mux\.Handle\|mux\.HandleFunc\|gin\.Router\|http\.Handle' --type=go
# 找出所有路由注册点,可能分布在多个文件
```

### 3.3 Types / PII struct(找 IDTYPE tag 的落点)

```bash
# 标准 go-zero
Glob 'internal/types/types.go'

# 非标(自研 / 多文件):grep 含有 PII 字段名的 struct
Grep -l 'type \w+(Request|Response|Req|Resp|DTO|Param|Model) struct' --type=go
# 然后逐个 Read 找 PII 字段(UserID / Email / Mac 等)
```

### 3.4 Middleware 栈(含前置 mw)

```bash
# 标准目录
Glob 'internal/middleware/*.go'

# 非标位置(middleware 可能直接在 handler / auth / 项目根)
Grep -l 'http\.HandlerFunc\|next http\.Handler' --type=go           # wrapper style
Grep -l 'func \(.*\) Handle(next' --type=go                          # go-zero middleware 签名
```

**识别前置 mw**(LoggerMiddleware 之前跑的):Read 路由文件(3.2 找到的),看 `WithMiddlewares([]Middleware{...})` 数组里 LoggerMiddleware 之前的项 — 那些就是前置 mw,内部打日志必须走 base+WithContext([go/sdk-usage.md §2](go/sdk-usage.md))。

### 3.5 Auth / UserID 提取函数

```bash
# 从 ctx 拿 user_id 的函数(命名各不相同)
Grep 'func \w*UserID\w*\(ctx context\.Context' --type=go
Grep 'func \w*GetUserID\w*\|func \w*ExtractUser\w*' --type=go

# 或者 JWT / session 提取模式
Grep 'ctx\.Value\(\w*userID\|ctx\.Value\(\w*userCtx' --type=go
```

LoggerMiddleware 模板里 `<project>.UserIDFromCtx(ctx)` 的占位替换成 recon 找到的实际函数名。

### 3.6 本地起服务方式

```bash
Read Makefile                  # 找 dev / run / start target
Read docker-compose*.yml       # dev 配置
Read README.md                 # "how to run" / "local development"
Read cmd/*/main.go             # 看 flag.Parse args,推断 go run 命令
```

探不到就留空 `<?>`,1-shot 报告明说"必填,等你给一条命令"。

---

## 4. SDK 现状检测

### Go

```bash
# 是否已装 a4x-logger-sdk(Go 模块)
Grep 'a4x-logger-sdk' go.mod
```

| 结果 | 解读 |
|---|---|
| 无命中 | 全新接入场景 |
| `gitlab.addx.ai/CLOUD/a4x-logger-sdk/go v0.2.2` | 已接入当前版本,可能是"升级 / 扩展" — 问用户具体想加什么 |
| `v0.1.x` 或更老 | 差量升级 — 需要 SDK CHANGELOG 确认 breaking changes |

### Python

```bash
# 检测 requirements / pyproject / setup 文件里有无 a4x-logger
Grep 'a4x-logger' requirements.txt pyproject.toml Pipfile setup.py setup.cfg 2>/dev/null

# 也用 import 路径做辅助判断(有 import 但没 requirements = editable install 或 monorepo)
Grep -nE 'from a4x_logger|import a4x_logger' --include='*.py' .
```

| 结果 | 解读 |
|---|---|
| 无命中(两条 grep 均 0) | 全新接入场景 |
| `a4x-logger @ git+...@python/v1.0.0` | 已接入 release 版本,可能是"升级 / 扩展" — 问用户具体想加什么 |
| `-e ../../python` 或 `-e git+...@python/v...` editable | 本地开发模式或 monorepo demo,已接入 |
| requirements 无命中但 import 有命中 | editable install(`pip install -e`) — 已接入,无版本锁 |

### Java

```bash
# 检测 pom.xml 里有无 a4x-logger
Grep 'a4x-logger' pom.xml
```

| 结果 | 解读 |
|---|---|
| 无命中 | 全新接入场景 |
| `<version>1.x.y</version>` | 已接入,按版本号判断是否需要升级 |
| Gradle 项目改用 `Grep 'a4x-logger' build.gradle build.gradle.kts` |

---

## 5. 1-shot 确认报告(Agent 输出格式)

Recon 跑完后,agent 直接输出以下结构给用户:

```markdown
我探查了你的项目,发现:

**SDK 源码**
- 路径:<path>(clone 到 / 沿用本机已有副本)
- 版本:<tag,如 go/v0.2.2>
- <如果本机副本 > 30 天:加一句 "副本是 X 个月前的,要不要 pull?">

**目标项目**
- 语言/框架:<Go 1.22 + go-zero v1.10 | Go + Gin | Java + Spring Boot | ...>
- 项目现状:**<全新接入 / 已有日志(来源:logx / stdlib log / 自研 / 混合)>**
  - 依据:grep 出 N 处 logx / M 处 stdlib log / K 处 logger / Logic 数 L
- 项目布局:<标准 go-zero(internal/logic/*Logic.go)| 非标(如 internal/<domain>/*_logic.go)>
- CI 类型:<GitLab + kaniko | GitHub Actions | 无 ...>
- 本地起法:<docker compose up -d | make dev | go run cmd/server/main.go -f etc/svc.yaml | **<?>(README 没写,你给一条命令)**>
- 已有 middleware:<recovery / authctx / UserId / ...>
- 缺(本接入需要补):<LoggerMiddleware>
- Auth 机制:<Bearer JWT | X-User-Id dev-mode | 自研 authctx | 无>
- SDK 现状版本:<无 / v0.1.5(升级场景)/ v0.2.2(已装最新)>

**接入计划**(按现状只列实际要做的)

<全新接入时输出:>
- 走 **全新接入** 模式
- 我会搭:主入口 SDK init / LoggerMiddleware / 路由挂上(<N> 个)/ Step 10 demo domain(1 Logic + test + types tag,可照抄可删)/ setup doc
- types tag / logic / test 都在 demo 里示范(项目目前没现成可改的)
- <无 CI 时:"项目无 CI,本次不配,会在交付总结里 flag 为 TODO">

<已有日志时输出:>
- 走 **已有日志迁移** 模式(源:<实际检测到的>)
- 我会改:主入口 SDK init / LoggerMiddleware / 路由(<N> 个) / types IDTYPE tag(<M> 个 PII 字段)/ <K> 个调用点(分 <B> 批 commit) / 前置 middleware(<P> 个)/ CI 鉴权 + Dockerfile / migration doc
- Step 5 按"机械替换 4 规则"(见 execution.md)改,**不发明 action 名,不强加 result=**

**确认后我开始建 baseline**(全新:跑 `go test` 记失败数;已有日志:再加 smoke 接口快照)

⚠️ **以下字段我留空了,需要你填**(空着我后面会卡):
- 标 "<...?>" 的字段 = 我探不到,等你回(尤其"本地起法"必填,Step 0 baseline 要起服务跑测试)
- 其他留空字段 = 探到的现状是"无",可忽略

有要修正的吗?(middleware 我探漏了 / auth 函数名不对 / 想换接入模式 / 等)
```

### Python 项目报告占位符对照

Go 模板的"Logic 数 L" / "标准 go-zero" / "go run cmd/server/main.go" 等 Go 概念,Python 项目按以下替换:

| Go 占位符 | Python 等价 |
|---|---|
| Logic 数 L | router / view / handler 数(`grep -rE '@app\.(get\|post\|put\|delete\|patch)' --include='*.py'` 或 Flask `@app.route`) |
| 标准 go-zero | FastAPI / Flask / Django / 其它(从 §1 探出的 framework) |
| go run cmd/server/main.go | `uvicorn main:app` / `gunicorn ...` / `python manage.py runserver` / etc.(从 §1 探出的 dev cmd) |
| go test ./... | `pytest` / `python3 -m pytest` |
| logger.yaml(go-zero) | `logger.yaml`(Python a4x-logger config) |

### Java 项目报告占位符对照

| Go 占位符 | Java 等价 |
|---|---|
| Logic 数 L | Service / Controller 方法数(`grep -rE '@(Get\|Post\|Put\|Delete\|RequestMapping)Mapping' --include='*.java'`) |
| 标准 go-zero | Spring Boot / Spring Web / 其它(从 §1 探出的 framework) |
| go run cmd/server/main.go | `mvn spring-boot:run` / `java -jar target/*.jar` |
| go test ./... | `mvn test` / `./mvnw test` |
| logger.yaml(go-zero) | `application.yml` / `logback-spring.xml` |

---

## 6. Recon 失败兜底

| 探不到的 | 怎么办 |
|---|---|
| go.mod / pom.xml 在非常规位置 | `Glob '**/go.mod' '**/pom.xml'` 深度扫;仍不命中问用户路径 |
| middleware 命名特殊(如 `AuthInterceptor` / `FilterChain` 等)| 1-shot 报告里如实说"我看到这些 wrapper 模式,不确定哪个是鉴权",让用户确认 |
| Routes 文件分布在多处 | 全部列出来,报告里说"检测到 N 个路由注册点" |
| 起服务方式 README 没写 | `<?>` 标记,明确让用户给一条命令 |
| SDK 源码访问不通(GitLab 网络故障)| 停,让用户排查网络 / token 后再来,**不继续** |

**绝不瞎猜**。探不到就如实留空 + 要求用户填,比"agent 自己发明" 稳。

---

## 7. 探查完的下一步

Recon 通过用户确认后:
1. 按对应语言的 execution.md 骨架执行(见 Phase 1 Dispatch 表)
2. Step 5(Go)/ Step 4(Java/Python)按上面探到的"源"走机械替换(任何源统一 recipe,不分叉)
3. Step 6(Go)处理前置 mw(§3.4 识别出来的那些);Java/Python 注意 Filter/Middleware 单写入方红线
4. types IDTYPE 加到 §3.3 找到的 struct / POJO / dataclass 文件里

---

## Phase 1 Dispatch

Recon 探出语言后,对应 execution.md 是唯一入口——不混跨语言步骤。

| Recon 出来语言 | 判据(Recon §1 结论) | 走哪个 execution |
|---|---|---|
| Go | `go.mod` 命中(`module`行 + `go-zero` / `gin` / `chi` 等 framework dep) | [references/go/execution.md](go/execution.md) |
| Java | `pom.xml` 命中,含 `spring-boot` / `spring-web` dep | [references/java/execution.md](java/execution.md) |
| Python | (主): `pyproject.toml` / `requirements.txt` 含 `fastapi` / `flask` / `django` 等;(备): 项目根 grep `from fastapi\|import fastapi\|from flask\|import flask\|from django` 在 `*.py` 文件命中 ≥ 1 | [references/python/execution.md](python/execution.md) |

**多语言混合服务**(同一 repo Go + Java / Go + Python):每语言独立 dispatch,分轮 recon → 分轮接入,不在同一 MR 混两语言。
