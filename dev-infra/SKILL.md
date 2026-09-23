---
name: dev-infra
description: 本地开发基础设施方法论（Dev Infrastructure Methodology）— AI Agent 友好的完整本地开发环境方法论。包含服务分层、端口发现、健康探活、热重载、make dev 生命周期、CLAUDE.md 规范固化、多 worktree 并发支持、Flutter Web E2E 等 12 条 Rules。只要用户提到多 worktree 开发、git worktree、端口冲突、并发开发环境、feature 分支隔离、本地环境搭建、多人协作开发冲突等关键词，就应该使用本 Skill。即使用户只是随口说"我们有几个 worktree 在同时开发"，也应触发本 Skill 来引导他们建立合理的分层环境。（曾用名 multi-worktree-dev → tdd-dev-infra → dev-infra；TDD 只是实现/验证方法，本 skill 是完整本地开发基础设施方法论）
---

# dev-infra

本地开发基础设施方法论（Dev Infrastructure Methodology）— AI Agent 友好的完整本地开发环境方法论。包含服务分层、端口发现、健康探活、热重载、make dev 生命周期、CLAUDE.md 规范固化、多 worktree 并发支持、Flutter Web E2E 等 12 条 Rules。（曾用名 multi-worktree-dev → tdd-dev-infra → dev-infra）

## 核心设计目标

| 目标 | 具体设计 |
|------|----------|
| 复杂度零 | 新建 worktree 无需手动配置任何文件，`make dev` 自动完成所有设置 |
| 并行安全 | 多 worktree 测试完全并行，互不阻塞 |
| 共享通道独占 | Feishu WS 等单例通道同一时刻只有一个 worktree 持有 |
| 层切换秒级 | Layer 1 常驻不重启，切换 worktree 只需启动 L2/L3 |
| 零端口冲突 | 端口通过 `find_free_port` 动态探测分配，代码里不允许写死端口号 |
| 开发流畅 | Layer 3 全部配置热重载，改代码不需重启进程 |

## Description

本 Skill 覆盖从零设计一个支持多分支并行开发的本地环境体系，核心思路是以下**三层分离**结构：

| 层级                          | 说明                                                           | 生命周期               |
| ----------------------------- | -------------------------------------------------------------- | ---------------------- |
| Layer 1 — 全局共享 Infra      | 数据库、缓存、消息队列（Kafka/RabbitMQ）、LLM 代理等重量级服务 | 常驻，所有分支共用     |
| Layer 2 — Worktree 独占 Infra | 需要独立的服务（如 Temporal、secret store）                    | 随 worktree 启动/停止  |
| Layer 3 — Host 进程           | 应用服务本体（API、Worker、前端）                              | 热重载，直接跑在宿主机 |


## Rules

### Rule 0 — 先扫描代码库，自动发现服务

收到分层设计请求后，**不要先问用户"你们有哪些服务"**。先主动扫描代码库，自动发现依赖，再给出有依据的分层建议。

**扫描顺序（按优先级）**：

1. **docker-compose 文件**（最直接）

   ```bash
   find . -name "docker-compose*.yml" -o -name "docker-compose*.yaml" | head -20
   ```

   解析其中的 `services:` 块，提取所有服务名和 image。

2. **requirements / 依赖清单**（推断中间件）

   ```bash
   # Python
   grep -rE "(redis|postgres|psycopg|sqlalchemy|temporalio|kafka|rabbitmq|celery|vault|consul)" \
     requirements*.txt pyproject.toml 2>/dev/null

   # Node.js
   grep -rE "(redis|pg|sequelize|temporal|kafka|amqp|consul)" \
     package.json 2>/dev/null

   # Go
   grep -rE "(go-redis|lib/pq|temporal|kafka|vault)" go.mod 2>/dev/null
   ```

3. **环境变量文件**（发现已有配置）

   ```bash
   cat .env.example .env.local.example 2>/dev/null | grep -E "_URL=|_HOST=|_PORT="
   ```

4. **Makefile / 启动脚本**（发现已有的 dev 工作流）

   ```bash
   grep -E "^(dev|start|up|test)" Makefile 2>/dev/null | head -20
   cat scripts/dev.sh scripts/start.sh 2>/dev/null | head -50
   ```

5. **Dockerfile 文件**（发现应用服务的构建目标）
   ```bash
   find . -name "Dockerfile*" | head -20
   ```
   解析每个 Dockerfile 所在目录和 `EXPOSE` 端口，推断这是哪个 L3 服务（vs. 第1步 docker-compose 里的 L1/L2 中间件）。

扫描完成后，整理发现的服务列表，结合 Rule 1 的决策树给出**预填的分层建议表**，让用户确认或修正，而不是让用户从头描述。

> **为什么先扫描？** 用户往往说不全所有依赖，而且说出来的服务名可能和代码里不一致。从代码库直接发现更准确，也能节省来回沟通时间。

### Rule 1 — 服务分层决策

基于 Rule 0 扫描结果，按以下决策树归档每个服务：

```
服务是否可以跨分支共享（数据覆盖不影响其他分支）？
  └─ 是，且启动成本高（> 30s） → Layer 1（全局共享）
       └─ 否，且需要分支间数据隔离 → Layer 2（worktree 独占，动态端口）
            └─ 否，是应用代码本体 → Layer 3（host 进程，热重载）
```

**常见归档示例**：

| 服务                  | 层级 | 理由                                       |
| --------------------- | ---- | ------------------------------------------ |
| PostgreSQL / MySQL    | L1   | 跨分支共享 schema，分支用不同 DB name 隔离 |
| Redis                 | L1   | key 前缀隔离即可                           |
| LiteLLM / 模型代理    | L1   | 无状态，共享安全                           |
| Kafka / RabbitMQ      | L1   | 消息队列共享，通过 topic/queue 隔离        |
| Temporal              | L2   | 需要 namespace 隔离                        |
| Vault / Consul        | L2   | 需要独立 secret 路径                       |
| API Gateway / Backend | L3   | 代码热重载，分支独立运行                   |
| 前端 Dev Server       | L3   | Vite HMR / Next.js dev                     |

> 另见 Rule 12a — 共享容器 + per-worktree DB name 的 variant（不为每个 worktree 各起一个 MySQL/Postgres 容器）。

### Rule 1b — AWS 依赖的本地化分级（docker-compose 优先，LocalStack 作为 fallback）

当 Rule 0 扫描到 AWS 服务依赖（boto3、aws-sdk、`s3://` URI、`dynamodb`/`sqs`/`sns` 等关键字、Terraform `aws_*` resource）时，按以下**优先级**决定 Layer 1 这一格放什么镜像。**LocalStack 不是 docker-compose 的替代方案——它本身就是一个 docker-compose service，归 Layer 1**。

**核心原则：尽可能真实（fidelity-first）。** 从最真实的实现开始往下退，每退一步都要有明确理由。

```
对每个 AWS 依赖：
  ① 有成熟开源等价镜像？（最真实，数据面 = 生产）
        ├─ 是 ──► docker-compose 跑该开源镜像                        【首选】
        │         RDS → postgres/mysql, ElastiCache → redis,
        │         MSK → confluent/cp-kafka, Flink → flink,
        │         OpenSearch → opensearch, S3 Vectors → pgvector/qdrant,
        │         DynamoDB → amazon/dynamodb-local（AWS 官方）,
        │         DocumentDB → mongo, Neptune → neo4j,
        │         Cognito → keycloak / authentik
        │
        └─ 否 ──► ② 是 AWS 控制面专有协议（Lambda / IAM / EventBridge / Step Functions / S3 SDK 兼容）？
                  │
                  ├─ 是 ──► ③ 服务在 LocalStack Community？
                  │         ├─ 是 ──► docker-compose 跑 localstack/localstack    【次选】
                  │         │         （事件链路、Lambda 真实执行最接近生产）
                  │         │
                  │         └─ 否 ──► ④ 团队有 LocalStack Pro license？
                  │                   ├─ 是 ──► docker-compose 跑 localstack-pro
                  │                   │         （RDS/Cognito/MSK 等专有服务）
                  │                   │
                  │                   └─ 否 ──► ⑤ 接真实 staging AWS（带 cleanup）
                  │                            （比 mock 更真实；成本可控时优先于 mock）
                  │
                  └─ 否 ──► ⑤ 接真实 staging AWS  ─或─  ⑥ Moto Server / 自写 stub
                            （Moto 是末位备选——快但仿真度最低，
                              仅做 CI 单元测试或 Pro 不可达时用）
```

**核心约束**：

- ✅ **真实优先**：能跑真服务（开源镜像 / 真 AWS staging）就不要 mock；能用 LocalStack 就不要 Moto
- ✅ docker-compose 开源镜像永远是 **#1 选项** —— 数据面行为和生产一致，CI 友好，无 license 成本
- ✅ LocalStack Community 是 **AWS 专有控制面**（Lambda 触发、事件链路、IAM 形态、S3 SDK）的**次选**——pin 版本（如 `localstack/localstack:3.x`），仓库已 2026-03-23 归档但镜像继续可用
- ✅ 真实 staging AWS 在很多场景下**比任何 mock 都真**——只要写好 setUp/tearDown，预算和速率允许就用
- ✅ 镜像同步到内部 Harbor，避免上游下架影响
- ❌ 禁止把 LocalStack 当 "docker-compose 替代品" —— 概念错误，LocalStack 自己就是 docker-compose service
- ❌ 禁止用 LocalStack 替代有成熟开源版的服务（RDS/Redis/Kafka/Flink/ES）—— 仿真度低于真实开源镜像
- ❌ 禁止默认用 Moto —— Moto 仿真度低于 LocalStack（不真跑 Lambda 代码、Pro 服务覆盖不全），仅作 CI 单元测试加速或 Pro 不可达时的降级
- ❌ 禁止把 `LOCALSTACK_AUTH_TOKEN` 提交到 Git —— Token 走 Vault 或 `.env.local`

**仿真度梯度（牢记这个序列）**：

```
真实生产 AWS  >  真实 staging AWS  >  开源等价镜像（postgres 等）
              >  LocalStack Pro    >  LocalStack Community
              >  Moto Server       >  自写 stub
```

每往下走一层，问自己一句：上一层为什么不行？

**Community 服务清单 + Tier 边界**：

详见 [`reference/localstack.md`](./reference/localstack.md)，含：
- §2 Community Edition 完整服务清单（39 个服务，附 OSS 源码目录交叉验证，最后核对 2026-04-30）
- §2.3 Community 支持但功能受限的"灰色地带"列表
- §3 Tier 边界查询的官方入口（Base/Ultimate 范围会持续变化，**不要把清单写死**——按需查 `https://docs.localstack.cloud/aws/licensing/` 和 `https://docs.localstack.cloud/aws/services/{service}/` 顶部的 `Included in Plans:` badge）
- §4.2 docker-compose 接入 LocalStack 的标准片段
- §4.3 LocalStack 与 Rule 1 / 2 / 4 / 4c / 9 的协同点

**LocalStack 健康探活**（接入 Rule 4c）：

```bash
# LocalStack 不能只看容器 running，要看内部服务都 ready
_check_localstack() {
  curl -sf --max-time 3 http://localhost:4566/_localstack/health \
    | jq -e '[.services[] | select(. != "running" and . != "available")] | length == 0' \
    >/dev/null \
    && echo "  ✓ localstack" \
    || echo "  ✗ localstack (services not all ready — curl /_localstack/health for detail)"
}
```

> **为什么 docker-compose 优先？** 三个理由：(1) 开源等价镜像的**数据面**行为和真服务一致，LocalStack 只擅长**控制面**；(2) 开源镜像无 license 风险，CI 可无限并发；(3) LocalStack OSS 已归档，新 AWS 服务不会进 Community，作为长期基础设施可持续性下降。

### Rule 2 — 端口管理（动态探测 + 零文件，运行时反查）

端口在 `make dev` 启动时通过 `find_free_port` + `flock` 互斥探测分配，**只活在进程环境变量里**，**绝不落盘**。任何时候需要再次知道某个服务正在 listen 哪个端口，就用 `pgrep -f <worktree-path>` 找到本 worktree 的进程，再用 `lsof -p <pid> -a -i TCP -sTCP:LISTEN` 从内核读出真实 listener。**代码里（vite.config、backend config、CLI 等）不允许写死端口号**，全部从环境变量读取。

**为什么不写 `.worktree.env` 文件**：

文件方案（曾经的 Rule 2）有一个无法回避的陈旧窗口——`.worktree.env` 写下 `BACKEND_PORT=8888` 之后，如果进程被 kill 掉、OOM、或者人手 `pkill`，端口立刻空出来；另一个进程（可能是别的 worktree、可能是无关程序）很容易就抢到 8888 重新 listen。这时候 `.worktree.env` 已经悄悄"过期"了——文件还说 8888 是你的，实际 listener 完全是别人。后续的 `source .worktree.env` + `curl localhost:8888/...` 会跑去问错的服务，开发者花半小时排查"为什么 health check 返回奇怪的 JSON"。

零文件方案每次都**现去问内核谁在 listen**（pgrep + lsof），不可能陈旧——读到的端口要么就是当前真正 listener 的端口，要么就是 `(not running)`，没有第三种可能。

**端口探测算法**（`scripts/dev-env.sh`）：

```bash
find_free_port() {
  local port="$1"
  local max=$(( port + 100 ))
  while [ "$port" -lt "$max" ]; do
    if ! nc -z localhost "$port" 2>/dev/null; then
      echo "$port"
      return 0
    fi
    port=$(( port + 1 ))
  done
  echo "ERROR: no free port in range $1-$max" >&2
  return 1
}
```

**运行时反查实际 listener**（任何脚本、任何时刻都可调用，不依赖任何持久化文件）：

```bash
# find_<svc>_pid — pgrep by the worktree's absolute path, fall back to lsof on port
find_go_pid() {
  pgrep -f "air.*${REPO_ROOT}" 2>/dev/null | head -1 && return 0
  # fallback：扫描文档化的端口范围，找到响应 /health 的那个
  local go_port
  go_port=$(find_go_port 2>/dev/null || true)
  [ -n "$go_port" ] && lsof -tiTCP:"$go_port" -sTCP:LISTEN 2>/dev/null | head -1
}

find_admin_pid() {
  pgrep -f "next dev.*${REPO_ROOT}/admin" 2>/dev/null | head -1 && return 0
  local admin_port
  admin_port=$(find_admin_port 2>/dev/null || true)
  [ -n "$admin_port" ] && lsof -tiTCP:"$admin_port" -sTCP:LISTEN 2>/dev/null | head -1
}

# get_listen_port <pid> — read the actual listening port from a running PID
get_listen_port() {
  lsof -nP -p "$1" -iTCP -sTCP:LISTEN 2>/dev/null \
    | awk 'NR>1{print $9}' | grep -oE '[0-9]+$' | head -1
}

# find_<svc>_port — probe the documented range for THIS worktree's health endpoint
find_go_port() {
  local port
  for port in $(seq 8000 8099); do
    if curl -sf --max-time 1 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
      echo "$port"
      return 0
    fi
  done
  return 1
}
```

**`flock` 做跨 worktree 分配互斥**：端口即便"不落盘"，多个 worktree 同时 `make dev` 时还是会抢同一个空闲端口。用 `flock` 把"探测 + 启动进程"串行化即可：

```bash
PORT_LOCK="/tmp/<project>-port-alloc.lock"

# In ensure_go_backend / ensure_admin:
exec 9>"$PORT_LOCK"
flock -w 30 9 || { log_err "port lock timeout"; return 1; }
GO_PORT=$(find_free_port 8000)
export GO_PORT
nohup air -- --port "$GO_PORT" > "$LOG_DIR/go.log" 2>&1 &
disown $!
# 等到进程真的 bind 上端口再释放锁（否则下一个 worktree 会拿到同一个 GO_PORT）
for _ in $(seq 1 30); do
  nc -z localhost "$GO_PORT" 2>/dev/null && break
  sleep 0.2
done
exec 9>&-
```

> **关键约束**：`flock` 段内必须做完"探测 + 启动进程 + 确认进程已 bind 端口"再释放锁，否则其他 worktree 会在你 `nohup` 之前拿到同一个端口。释放后端口就归属本 worktree 的进程，任何时候 pgrep+lsof 反查都能拿到它。

**代码侧读取方式**（直接从进程环境变量读，无文件、无 fallback）：

```typescript
// vite.config.ts — 端口来自 process.env，缺失则抛错而不是默认 5173
const frontendPort = process.env.FRONTEND_PORT;
const backendPort = process.env.BACKEND_PORT;
if (!frontendPort || !backendPort) {
  throw new Error('FRONTEND_PORT / BACKEND_PORT 未设置——请通过 make dev 启动，不要直接 vite dev');
}
export default defineConfig({
  server: {
    port: Number(frontendPort),
    proxy: { '/api': { target: `http://localhost:${backendPort}` } },
  },
});
```

```python
# config.py — pydantic_settings 从进程环境变量读取，无文件、无默认
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    BACKEND_PORT: int   # 无默认值——make dev 会 export，缺失即抛 ValidationError
    DATABASE_URL: str   # 同上

settings = Settings()
```

> **零文件**只覆盖端口（瞬时运行时状态）；其他持久状态（feature flag、共享通道 owner、API key）走各自适合的承载方式（见 Rule 3 / Rule 9 / `.env.local`）。

### Rule 3 — 共享通道的排他切换

Feishu WS、WebSocket 推送入口等**全局单例通道**，同一时间只能由一个 worktree 持有。

**切换机制设计要求**：

1. `make dev` 触发时，自动执行 `scripts/feishu-switch.sh $(PWD)`
2. 脚本遍历所有 worktree，向其他 worktree 发送"关闭通道"指令（更新各 worktree 的 `.feishu-ws-enabled` flag 文件，gateway 的 reloader 监听该文件）
3. 仅在当前 worktree 开启通道
4. `make test*` **不触发**此脚本，测试不抢占共享通道

> 持久状态（如 feishu WS owner）允许小型**专用 flag 文件**承载；这与 Rule 2 的零文件原则不冲突——Rule 2 只禁止把**端口**这类瞬时运行时状态落盘。`.feishu-ws-enabled` 只承载一个 boolean，被 kill/抢占也不会引起端口陈旧那种 silent drift。

**feishu-switch.sh 逻辑骨架**：

```bash
#!/usr/bin/env bash
CURRENT_WT="$1"

# 遍历所有 worktree
git worktree list --porcelain | grep "^worktree " | awk '{print $2}' | while read wt; do
  if [ "$wt" != "$CURRENT_WT" ]; then
    # 关闭其他 worktree 的 Feishu WS（写入专用 flag 文件，gateway 的 reloader 监听该文件）
    echo "false" > "$wt/.feishu-ws-enabled"
    # 触发对应 gateway 热重载（uvicorn --reload 会检测到文件变化）
    touch "$wt/services/gateway/main.py"
  fi
done

# 开启当前 worktree
echo "true" > "$CURRENT_WT/.feishu-ws-enabled"
```

### Rule 4 — 幂等的 ensure-up 模式

所有启动脚本必须**幂等**：多次调用结果一致，不重复创建资源。

```bash
# 推荐模式：先检查，再启动
ensure_service_up() {
  local service=$1
  if ! docker ps --filter "name=${service}" --filter "status=running" -q | grep -q .; then
    docker compose up -d "${service}"
  fi
}
```

`make test*` 内部调用 `ensure_service_up`，无需先手动 `make dev`。

### Rule 4b — 跨平台后台进程启动

Layer 3 Host 进程需要脱离父 shell（`make` 结束时不被 SIGHUP 杀死），不同系统命令不同：

| 平台 | 推荐方式 | 说明 |
|------|---------|------|
| **macOS** | `nohup cmd & disown $!` | `setsid` 在 macOS 不可用 |
| **Linux** | `nohup cmd & disown $!` | 同样适用；`setsid nohup cmd &` 也可以但无必要 |
| **Windows (Git Bash/WSL)** | `nohup cmd & disown $!` | Git Bash 内置 `nohup`，`disown` 为 bash builtin |
| **Windows (原生 cmd/PowerShell)** | `Start-Process -NoNewWindow` / `start /B` | 仅在不用 bash 的场景下 |

**跨平台统一写法**（在 `bash`/`zsh` 脚本中）：

```bash
_start_bg() {
  local logfile="$LOG_DIR/${1}.log"
  mkdir -p "$LOG_DIR"
  nohup "${@:2}" > "$logfile" 2>&1 &   # nohup: 忽略 HUP 信号
  local pid=$!
  disown "$pid"                          # disown: 从 shell job 表移除，make 退出不传播 HUP
  echo "$pid"
}

# 使用
pid=$(_start_bg "gateway" bash -c "cd services/gateway && uvicorn src.main:app --reload")

```

> **常见陷阱**：`setsid` 是 Linux-only（util-linux 包），在 macOS 上报 `command not found`。
> `nohup + disown` 是 POSIX 兼容方案，三平台统一。

> **disown 作用域限制**：`nohup cmd & disown $!` 必须在**同一 shell 上下文**（同一函数体或同一 `{}` block）中执行，不能跨 `&&` 链。在 `&&` 链里的 subshell 里 `disown` 会找不到 job。推荐封装成函数（如上方 `_start_bg`）确保作用域正确。


### Rule 4c — 启动后实际健康验证（不许假装 Ready）

`make dev` 在 Layer 3 进程启动后，**必须做真实端口/HTTP 探活**，而不是直接打印 "=== Dev environment ready ==="。

**正确的验证逻辑**：

```bash
_check_health() {
  local name="$1" url="$2" port="$3"
  if [ -n "$url" ]; then
    curl -sf --max-time 3 "$url" >/dev/null 2>&1 \
      && echo "  ✓ ${name}" \
      || echo "  ✗ ${name} (not ready — check .logs/${name}.log)"
  else
    nc -z localhost "$port" 2>/dev/null \
      && echo "  ✓ ${name}:${port}" \
      || echo "  ✗ ${name}:${port} (not ready — check .logs/${name}.log)"
  fi
}

sleep 2  # brief grace for port binding
_check_health "gateway"   "http://localhost:${GATEWAY_PORT}/health" ""
_check_health "sandbox"   "http://localhost:${SANDBOX_PORT}/health" ""
_check_health "temporal"  "" "${TEMPORAL_HOST_PORT}"
```

**强制要求**：
- ✅ 每个 Layer 3 服务都必须有端口或 HTTP 健康探活
- ✅ 失败时输出 `✗ service (not ready — check .logs/service.log)` 并指引日志路径  
- ✅ Layer 3 进程 PID 不代表服务已可用（uvicorn 需要 1-2s 绑定端口）
- ❌ 禁止只检查 PID 存在就声明 ready
- ❌ 禁止 "Dev environment ready" 在任何服务实际 down 的情况下出现

**Vault 等可选服务**：若服务未配置（Docker 无法拉取镜像等），显示 `(not running)` 而不是 `✗`，不阻断启动流程。

### Rule 5 — 测试并行安全

测试命令设计规范（命名遵循 `testing-strategy` Skill 的 L1/L2/L3 分层约定）：

| 层级 | 命令 | Infra 依赖 | 是否影响共享通道 |
|------|------|-----------|----------------|
| L1 Unit | `make test-unit` | 无（纯代码）| ❌ |
| L2 Integration | `make test-l2` | L1 + L2（自动 ensure）| ❌ |
| L3 E2E | `make test-l3` | L1 + L2 + L3 服务 | ❌ |
| L4 UAT | `make test-l4-uat` | 全部（需真实 Staging 环境）| ❌ |
| Smoke | `make smoke` | 全部（需 make dev 先跑）| ❌ |
| Full Regression | `make regression` | 全部（CI 质量门禁）| ❌ |

> **核心约束：`make test*` 系列命令对 Feishu WS 等单例通道——不开、不关、不动。**
>
> 具体说：
> - ❌ 不调用 `feishu-switch.sh`（不抢、不切换）
> - ❌ 不设置 `FEISHU_WS_ENABLED=true`（不主动开启）
> - ❌ 不设置 `FEISHU_WS_ENABLED=false` 并重启 gateway（不主动关闭）
> - ✅ 保持当前 worktree 的 WS 状态原样，测试结束后状态不变
>
> **目的**：在 worktree-A 跑 `make dev` 持有 WS 期间，worktree-B 可以安全地 `make test-l2` 而不打断 A 的连接。

分层测试详细策略（覆盖率门禁、Mock 隔离、TDD 调试、CI pipeline 配置）参考 `testing-strategy` Skill。


### Rule 6 — 热重载与 Watch 自动构建

Layer 3 服务应全部配置热重载，代码改动**不需要手动重启进程**。

**按服务类型选择 watch 工具**：

| 服务类型 | 推荐工具 | 启动方式 | 触发条件 |
|---------|---------|---------|--------|
| Python API（FastAPI/Flask） | `uvicorn --reload` | `uvicorn app.main:app --reload --reload-dir src` | `.py` 文件变更 |
| Python Worker（Temporal/Celery） | `watchfiles` | `watchfiles 'python worker.py' src/` | `.py` 文件变更 |
| Node.js / TypeScript Worker | `tsx watch` / `nodemon` | `tsx watch src/worker.ts` | `.ts/.js` 变更 |
| 前端（React/Next.js/Vite） | 内置 HMR | `vite dev` / `next dev` | 浏览器即时局部刷新，无需手动刷新 |
| Go 服务 | `air` | `air -c .air.toml` | `.go` 文件变更自动重编译 |
| 静态文件 / 配置 | `watchexec` | `watchexec -e yaml,toml -- ./restart.sh` | 配置文件变更触发重启脚本 |
| Flutter Web | `flutter build web` + Vite 托管 | 先 build，Vite 托管 `/build/web/`，开发时 `flutter run -d chrome` | Dart 文件变更触发 Hot Restart |

**多 worktree 注意事项**：每个 worktree 的 watch 进程相互独立，端口已通过 Rule 2 的 offset 隔离，无需额外处理。

#### 容器内热加载（Volume Mount 模式）

对于以**容器方式**运行的 L3 服务（非 host 进程），热加载靠**挂载源码目录**实现，而非重建镜像：

```yaml
# docker-compose.dev.yml — 开发模式，挂载宿主机源码
services:
  sandbox:
    image: clawplex/sandbox:latest   # 镜像只提供运行时环境
    volumes:
      - ./services/sandbox:/app/src  # 宿主机源码 → 容器内
    command: tsx watch /app/src/index.ts  # 容器内 watcher 监听变更
    environment:
      - NODE_ENV=development
```

```yaml
# docker-compose.prod.yml — 生产模式，代码 baked 进镜像
services:
  sandbox:
    image: clawplex/sandbox:${IMAGE_TAG}  # 代码已在镜像构建时打包
    # 无 volumes，无 watcher，代码不可变
```

**开发 vs 生产的核心区别**：

| 维度 | 开发模式（volume mount） | 生产模式（baked image） |
|------|------------------------|----------------------|
| 代码来源 | 宿主机目录实时挂载 | 构建时固化进镜像 |
| 热重载 | ✅ 容器内 watcher 监听 | ❌ 改代码需重建镜像 |
| 镜像构建 | 无需（启动快） | CI 构建 → Harbor 推送 |
| 隔离性 | 低（与宿主机共享源码） | 高（完全独立） |
| 适用场景 | 本地开发迭代 | staging / prod 部署 |

**在多 worktree 环境中**，开发模式的 volume mount 路径已含 worktree 的绝对路径，不同 worktree 自然隔离，无需额外处理。

**扫描项目已有 watch 配置**（Rule 0 扫描时一并检查）：

```bash
# 检查现有 docker-compose 是否区分了 dev/prod 模式
ls docker-compose*.yml
grep -l "volumes:" docker-compose*.yml   # 有 volumes 的是 dev 风格
grep -l "NODE_ENV=production" docker-compose*.yml  # 生产模式

# 检查各服务是否已配置 watch
grep -rE "(--reload|watchfiles|nodemon|tsx watch|air|HMR)" \
  Makefile scripts/*.sh 2>/dev/null
```

如发现项目只有一个 `docker-compose.yml` 且未区分 dev/prod，建议拆分成 `docker-compose.yml`（base）+ `docker-compose.dev.yml`（开发 override）。

### Rule 7 — Dev 生命周期命令设计

`make dev` 不是唯一命令，需要设计完整的生命周期命令集：

| 命令 | 行为 | 典型实现 |
|------|------|--------|
| `make dev` | 启动当前 worktree（L2 + L3），切换共享通道 | ensure L1 → ensure L2 → start L3 with watch → feishu-switch |
| `make dev-down` | 停止当前 worktree（L2 + L3 停止，**L1 继续跑**） | stop L3 pids → docker compose stop L2 services |
| `make dev-check` | 检查所有服务健康状态 | curl 各服务 health endpoint，列出 UP/DOWN |
| `make dev-restart` | 快速重启 L3 进程（不重启 L2） | kill L3 pids → restart with watch |

**E2E 测试的两种模式**（适用于有外部依赖 API 的 L3 服务）：

| 模式 | 外部依赖处理 | 适用阶段 |
|------|------------|---------|
| **Mock 模式**（日常） | 用 mock server 模拟外部 API | 日常开发迭代，启动快，无额外依赖 |
| **真实模式**（验收前） | 接入真实外部服务/环境 | 发布前验收，验证真实集成行为 |



**前置条件检查**（`make dev` 启动前自动验证）：

```bash
check_prerequisites() {
  command -v docker >/dev/null || { echo "❌ docker not found"; exit 1; }
  command -v uv >/dev/null    || { echo "❌ uv not found"; exit 1; }
  command -v node >/dev/null  || { echo "❌ node not found"; exit 1; }
  docker info >/dev/null 2>&1 || { echo "❌ Docker daemon not running"; exit 1; }
  echo "✅ prerequisites OK"
}
```

### Rule 8 — 文档结构（Quad-File HTML 模式）

每个有独立部署复杂度的服务，项目文档按以下四个 HTML 文件组织。Markdown 只保留给 `CLAUDE.md` / `AGENTS.md` 这类 agent 指令文件，不作为部署文档 authoring SSOT：

```
docs/deployment/
  overview.html    # 架构总览、Mermaid 流程图（本地→CI→CD）
  local-dev.html   # 分"用户指南"和"实现方案"两节
  ci.html          # CI pipeline 设计、测试策略
  cd.html          # K8s / Terraform / ArgoCD 配置
```

`local-dev.html` 必须分两节：

- **用户指南**：只写命令和结果，给开发者看
- **实现方案**：解释为什么这样设计，给维护者看

### Rule 9 — 操作红线

- **禁止**修改 Layer 1 的 docker-compose 配置而不通知其他在跑的 worktree
- **禁止**在 `make test*` 中调用共享通道切换脚本
- **禁止**把端口号写进任何文件（包括 `.env` / `.worktree.env` / configs）—— 端口只活在进程 env 里，运行时反查；持久 feature flag 用小型专用文件（如 `.feishu-ws-enabled`），不混在端口承载文件里
- **禁止**在多 worktree 环境中硬编码端口号（必须用环境变量 + offset）
- Layer 1 服务挂掉时**先排查共享资源**再重启，避免影响其他在测试的 worktree
- **裸命令跑了必须同步更新 make/scripts**：直接跑 `docker compose up/down`、`uvicorn`、`pytest` 等会改变系统状态（启动进程、占用端口、写 pid 文件），但 make 和 scripts 不知道——下次 `make dev` 时端口已被占用、pid 文件过期、infra 状态不匹配，导致 make 失效。
  - 如果临时用了裸命令调试，跑完务必要么 **kill 掉手动起的进程**，要么 **把变更固化进 scripts/**，保持 make 和实际状态一致。
  - ✅ 裸命令调试 → 调试完 kill 进程 → `make dev` 接管
  - ❌ 裸命令起了 sandbox，不 kill，直接跑 `make dev` → port already in use
- **禁止裸命令**：所有开发/测试操作必须走现有 `make` targets。先看已有 targets 能否覆盖需求，不行就看能否通过参数组合实现。
- **make targets 是固定的，禁止随意新增**：允许的 targets 是固定的一套：
  - 开发生命周期：`make dev`、`make dev-check`、`make dev-down`
  - 测试：`make test-unit`、`make test-l2`、`make test-l3`、`make test-l4-uat`、`make smoke`、`make regression`、`make test-cov`、`make test-watch`
  - 构建/质量：`make lint`、`make format`、`make build-*`
  - 不属于以上任一类别的新 target，一律不加。

### Rule 10 — 写 CLAUDE.md：把 `make dev` 规范固化到项目

为项目设计或落地 `make dev` 后，**必须将以下规范写入项目根目录的 `CLAUDE.md`**（如无则新建）。这样后续所有开发者和 AI Agent 进入项目时自动遵守，不需要口头传达。

**必须写入的内容（至少包含）**：

```markdown
## 工具链

- **启动开发环境**：只能用 `make dev`。禁止手动 `uvicorn`/`vite` 启动，端口由 `make dev` 启动时动态探测 + `flock` 互斥分配（Rule 2 零文件），运行时通过 `pgrep`+`lsof` 反查实际 listener；代码里不允许写死端口号。
  - `make dev` — 启动全部
  - `make dev-down` — 停止全部
  - `make dev-restart` — 重启应用进程
  - `make dev-check` — 检查服务健康
```

**写入时机**：
- 新项目首次设计 `make dev` 时 → 写入
- 已有项目补全 `make dev` 后 → 追加到已有 CLAUDE.md
- 如果 CLAUDE.md 已有 `工具链` 章节，追加到该章节下；否则新建章节

**为什么**：CLAUDE.md 是 Claude Code 进入项目时必读的指令文件。把 `make dev` 写进去，等于给每个新对话自动注入"禁止裸命令"的约束，比 Skill 触发更可靠。

#### Multi-Component monorepo：根 + 每个 Component 子目录都要有 CLAUDE.md

**触发条件**：monorepo 同时含多个 Component（如 `server/` Go、`admin/` Next.js、`app/` Flutter、`moments/` React），各自栈、make targets、相关 skill 都不一样。

**做法**：
- **根 CLAUDE.md**：写**全栈 `make dev`** 规约（根 Makefile 编排所有 Component、全局禁止裸命令、跨 Component 端口/共享通道协议）—— 即上一节的内容
- **每个 Component 子目录也落一份自己的 CLAUDE.md**（30-50 行，[模板见 architect skill](../architect/SKILL.md#子目录-claudemd-模板)），标注：
  - 本 Component 的栈（Go / Next.js / Flutter / ...）和当前阶段
  - 在该子目录工作时**必读的 skill** 列表（如 `server/` 必读 `microservice-integrate`，`admin/` 不直连后端 DB 必走 backend API）
  - 本 Component 的边界（关键 ADR、不准直连的依赖、数据所有权）
- 每个子 CLAUDE.md 同目录加 `AGENTS.md` 相对路径软链（`ln -s CLAUDE.md AGENTS.md`），让 Codex / Aider 等读 `AGENTS.md` 的工具共享同一份指引
- **保持薄**：子 CLAUDE.md 只叠加本 Component 特有信息，**不复述**根 CLAUDE.md 已有的项目级规则
- **架构变更同步更新**：拆 Component、栈切换、新增模块、ADR 推翻等，子 CLAUDE.md 同步改，不允许漂移

**为什么**：根 CLAUDE.md 解决"项目级共同约定"，但 AI Agent 进入 `admin/` 后，根 CLAUDE.md 不会告诉它"这里要用 `microservice-integrate` 调后端、不许直连 SQL"。子 CLAUDE.md 是 Agent 进到子目录时**就近**注入的本地上下文面包屑——比根文件更精确，比口头传达更可靠。

### Rule 11 — Flutter Web E2E 测试（Playwright + Semantics Tree）

Flutter Web 的 E2E 测试通过 Playwright 操作 Flutter Semantics tree（无障碍 DOM 树），无需修改 Flutter 生产代码。

**核心方法**（借鉴 iot-system-template）：

| 步骤 | 说明 |
|------|------|
| 等待就绪 | 轮询 `<flutter-view>` 或 `<flt-glass-pane>` 出现 |
| 启用 Semantics | 点击 Flutter 内置 "Enable accessibility" 按钮 |
| 元素定位 | 通过 ARIA role 属性（`role="button"`, `role="switch"` 等）定位 |
| 交互 | 标准 Playwright click/fill/assert 操作 |

**Playwright 配置要点**：

```typescript
// playwright.config.ts
export default defineConfig({
  testDir: './tests/l3-browser',
  timeout: 60_000,           // Flutter Web 加载慢
  expect: { timeout: 15_000 },
  workers: 1,                // Flutter Web 不支持并行
  projects: [{
    name: 'chromium',
    use: {
      headless: true,
      screenshot: 'only-on-failure',
      video: 'retain-on-failure',
    },
  }],
  webServer: {
    command: 'npm run dev',  // 或 vite dev
    port: 5173,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
```

**测试辅助函数**：

```typescript
async function waitForFlutterReady(page: Page) {
  await page.waitForSelector('flutter-view, flt-glass-pane', { timeout: 30_000 });
}

async function enableFlutterSemantics(page: Page) {
  const btn = page.locator('flt-semantics-placeholder');
  if (await btn.isVisible()) await btn.click();
}
```

**多 worktree 注意**：Playwright webServer 端口由 `make dev` 动态分配（Rule 2），不同 worktree 各自 `pgrep`+`lsof` 反查实际端口，互不冲突。

### Rule 12 — 共享容器 + 自举脚本（customer-care variant）

Rule 1 / Rule 4 给了一套"默认推荐"——每个 worktree 一个独立容器 + 测试前要先 `make dev`。但在 customer-care 项目里，我们选了另一条更激进的路径：**数据库容器全局复用**、**`make test-*` 自己把前置条件补齐**。它不是对 Rule 1/4 的替代，而是一组并列 variant——当你的项目满足对应触发条件时，可以按这里的写法落地。

> 原 Rule 12a（零文件端口）已被提升为 Rule 2 的正式做法（删除了 `.worktree.env` 文件方案）。本节剩下的两个 variant 专门覆盖 **L1 容器** 和 **`make test-*` 自举** 两件事，不再包含端口方案。

#### Rule 12a — 共享容器 per-DB 隔离（variant of Rule 1）

针对 MySQL / PostgreSQL 这类**容器启动慢（`initdb` / 密码初始化要 10–30s）+ schema 迁移成本高**的 L1 服务，Rule 1 的"每个 worktree 独立容器"会放大三项代价：容器数量随 worktree 数线性增长、每切 worktree 要等一次初始化、迁移脚本在每个容器里各跑一遍。

**做法**：全局共享一个容器（如 `customer_care_mysql_dev`），每个 worktree 用**独立 DB name**：

```bash
WORKTREE_ID=$(printf '%s' "$(pwd)" | sha1sum | cut -c1-8)
MYSQL_DATABASE="customer_care_${WORKTREE_ID}"

# 容器只启一次；DB 在第一次 make dev 时 CREATE DATABASE IF NOT EXISTS
docker exec -i "$MYSQL_CONTAINER" mysql -u root -p"$MYSQL_ROOT_PASSWORD" <<SQL
CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\`;
GRANT ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'%';
FLUSH PRIVILEGES;
SQL

# 每个 worktree 单独 push 自己的 schema
DATABASE_URL="mysql://${MYSQL_USER}:${MYSQL_PASSWORD}@127.0.0.1:${MYSQL_HOST_PORT}/${MYSQL_DATABASE}" \
  npx prisma db push --accept-data-loss
```

| 维度 | Rule 1（per-worktree 容器） | Rule 12a（共享容器 + per-DB） |
|------|------------------------------|-----------------------------|
| 容器数 | N 个 worktree = N 个容器 | 始终 1 个容器 |
| 首次启动耗时 | 每次新 worktree ≈ 10–30s | 仅首次，切 worktree ≈ 0 |
| schema 迁移 | 每个容器独立一份 | 每个 DB 独立一份（`prisma db push` 在各自 DB 上） |
| `--accept-data-loss` 风险 | 天然隔离，不会误伤其他 worktree | **也不会**——每个 DB 名不同，`db push` 只动本 worktree 的 DB |
| 磁盘/内存占用 | 高（每个实例各占一份 innodb buffer pool） | 低（1 份 buffer pool） |
| 前提条件 | 无 | DB 配置必须支持"同实例多 DB"（MySQL/PostgreSQL 原生就支持） |

**WORKTREE_ID 选择**：用 `sha1sum` 取 `$(pwd)` 的前 8 位作为稳定 ID——同一 worktree 路径永远映射到同一 DB 名，换分支不会换 DB；不同 worktree 路径几乎不会碰撞。

> **适用边界**：这个 variant 只对"启动慢 + 可多 DB"的中间件生效。Redis 不需要（用 key 前缀更简单），Kafka 也不需要（用 topic 前缀）。Temporal 反而**不适合**——Temporal 的 namespace 隔离没有数据库级那么彻底，worker 还是可能串 task queue。

#### Rule 12b — 自举脚本（`make test-*` 缺啥补啥）

与 Rule 4 "ensure-up 模式" 协同——但把范围从"L1/L2 服务"扩展到了**整个前置条件链**：`make test-e2e-api` 在真正跑 Playwright 之前，自己依次检查并**自动补齐**所有依赖，不要求开发者先记着跑一遍 `make dev`。

**6 步流水**（customer-care 的 `Makefile` 第 186–260 行 `test-e2e-api` target）：

```makefile
# 1. 检查 flock (util-linux)
# 2. 检查 Docker daemon
# 3. 检查/启动 make dev (MySQL + Go Backend + Admin)
# 4. 检查/应用 migrations
# 5. 发现动态端口并注入环境变量
# 6. 运行 Playwright
test-e2e-api:
	@bash -c 'set +u; \
		echo "==> [1/6] 检查 flock..."; \
		command -v flock >/dev/null 2>&1 || { echo "✗ brew install util-linux"; exit 1; }; \
		echo "==> [2/6] 检查 Docker daemon..."; \
		docker info >/dev/null 2>&1 || { echo "✗ 请打开 Docker Desktop"; exit 1; }; \
		source $(DEV_ENV); \
		WORKTREE_ID=$$(echo -n "$$(pwd)" | shasum | cut -c1-8); \
		MYSQL_DATABASE=customer_care_$$WORKTREE_ID; \
		GO_PORT=$$(find_go_port 2>/dev/null || true); \
		MYSQL_HOST_PORT=$$(docker port "$$MYSQL_CONTAINER" 3306/tcp 2>/dev/null | awk -F: "NR==1 {print \$$NF}" || true); \
		if [ -z "$$MYSQL_HOST_PORT" ] || [ -z "$$GO_PORT" ]; then \
			echo "==> [3/6] make dev 未完全启动，正在启动..."; \
			ensure_mysql || exit 1; \
			echo "==> [3.5/6] 应用 migrations (DB: $$MYSQL_DATABASE)..."; \
			for f in e2e/migrations/*.sql; do \
				docker exec -i "$$MYSQL_CONTAINER" mysql -u root -p"$$MYSQL_ROOT_PASSWORD" "$$MYSQL_DATABASE" < "$$f"; \
			done; \
			ensure_minio || exit 1; ensure_growthbook || exit 1; ensure_go_backend || exit 1; \
			GO_PORT=$$(find_go_port); \
		else \
			echo "==> [3/6] make dev 已运行"; \
		fi; \
		echo "==> [4/6] 前置检查完成"; \
		echo "==> [5/6] 检查 Playwright 依赖..."; \
		[ -d "e2e/api-e2e/node_modules" ] || (cd e2e/api-e2e && npm install --silent); \
		echo "==> [6/6] 运行 Playwright E2E..."; \
		cd e2e/api-e2e && \
		API_BASE_URL="http://localhost:$$GO_PORT" DB_HOST=127.0.0.1 DB_PORT="$$MYSQL_HOST_PORT" \
		DB_NAME="$$MYSQL_DATABASE" npx playwright test'
```

**关键点**：

1. 每一步都带 `==> [X/6]` 进度 echo，开发者即使不看脚本也知道卡在哪
2. **前置条件不满足时不是 fail fast，而是自动补**——`ensure_mysql` / `ensure_go_backend` 等函数已经是 Rule 4 的幂等 ensure 模式，这里只是把它们串起来调用
3. 端口注入采用 Rule 2 的"运行时发现"：`find_go_port` + `docker port` 读出实际端口，直接塞进 Playwright 的 `API_BASE_URL` / `DB_PORT`

| 维度 | 普通 `make test-*`（假设 `make dev` 已跑） | Rule 12b 自举 |
|------|-----------------------------------------|--------------|
| 前置条件 | 开发者必须记得先 `make dev` | 无，直接 `make test-e2e-api` |
| CI 脚本 | `make dev && make test-e2e-api`（两条命令） | `make test-e2e-api`（一条命令） |
| 失败信息 | "connection refused"（不清楚原因） | "✗ Docker Desktop 未运行" / "✗ flock 未安装" |
| 和 Rule 4 的关系 | Rule 4 只 ensure L1/L2 | Rule 12b 把 L3 + 迁移 + 依赖安装全串起来 |

> **价值**：CI 和本地执行完全同一条命令，不需要分别写 CI YAML 里的前置 step 和本地文档里的"先 make dev"说明——减少一整类 CI/本地不一致的 bug。

## Examples

### Bad

```
- Temporal 放 Layer 1（所有 worktree 共用同一个 Temporal namespace）
  → 问题：worktree-A 的 Workflow 会被 worktree-B 的 Worker 捡走执行

- make test 里调用 feishu-switch.sh
  → 问题：跑个测试就把同事的 Feishu 连接抢走了

- 把 GATEWAY_PORT=8001 硬编码进 vite.config.ts 或 config.py
  → 问题：第二个项目/worktree 启动时直接端口冲突

- 落地了 make dev 但没写进 CLAUDE.md
  → 问题：下次对话 Agent 不知道规范，又手动 uvicorn 起服务

- 把端口写进 .worktree.env / .env / config.yml
  → 问题：进程死掉/被抢占后文件陈旧，下次启动读到错的端口
```

### Good

```
- Temporal → Layer 2（每个 worktree 独立端口 + namespace）
- make test* → 只 ensure L1/L2，不调用 feishu-switch.sh
- 端口通过 find_free_port + flock 动态分配，运行时 pgrep+lsof 反查，无文件落盘，代码零硬编码
- CLAUDE.md 写入 `make dev` 规范，Agent 进入项目自动遵守
- customer-care 模式：共享 MySQL 容器 + per-worktree DB name (Rule 12a) + make test-* 自举 (Rule 12b)

多项目/多 worktree 并发开发：
  项目 A: make dev → frontend :5173, backend :8000 (自动分配)
  项目 B: make dev → frontend :5174, backend :8001 (自动避开 A 的端口)
  切换: make dev-down → 另一个 make dev（秒级）
```

## References

- [testing-strategy Skill](../testing-strategy/SKILL.md) — L1/L2/L3/L4 分层测试完整策略，含 CI 质量门禁和 TDD 调试流程
- [ClawPlex 本地开发环境文档](../../ClawPlex/.claude/worktrees/local-dev/docs/deployment/local-dev.html) — L1/L2/L3 分层的参考实现
- [ClawPlex 部署架构总览](../../ClawPlex/.claude/worktrees/local-dev/docs/deployment/overview.html) — Mermaid 架构图 + CI/CD 全链路
