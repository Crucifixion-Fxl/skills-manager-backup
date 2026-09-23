# Regression Smoke:Agent 执行指南

**Agent 在 skill "建立 baseline / 每批 diff / 合并前 gate" 环节执行本指南**:读目标项目 → 生成定制版 smoke 脚本 → 跑 baseline + per-step diff → commit 脚本进 repo。

按下面 4 phase 顺序执行。

---

## Phase 1:Discover(用 Grep / Read / Glob)

不写任何代码之前,先探查目标项目。每一项都是一次 Agent tool 调用,结果决定 Phase 2 的生成策略。

### 1.1 发现真实 endpoint

优先级(按找到一个就停):

| 证据类型 | 怎么找 |
|---|---|
| goctl `.api` 文件 | `Glob '**/*.api'`,`Read` 看 `@handler` / `get/post/put/delete` 行 |
| goctl 生成的 routes.go | `Glob 'internal/handler/routes.go'` 或 `Glob '**/routes.go'`,`Read` 找 `rest.Route{Method, Path, ...}` |
| OpenAPI / Swagger | `Glob '**/swagger*.{json,yaml}'` / `Glob '**/openapi.*'` |
| net/http 手写 | `Grep 'http\.HandleFunc\|mux\.Handle\|r\.(Get\|Post\|Put\|Delete)' --type=go` |
| Gin / Echo / Chi | `Grep 'router\.(GET\|POST\|PUT\|DELETE)\|e\.(GET\|POST)' --type=go` |

记下:
- 每个 endpoint 的 method + path
- 关联的 domain(从目录结构或 URL prefix)
- 是否需要 auth(看路由组的 middleware 挂载情况)

### 1.2 发现鉴权机制

```bash
# 找 middleware 实现
Grep 'JWT\|jwt\|Authorization\|X-User-Id\|CtxKeyUserID\|session\|cookie' --type=go
```

识别模式:

| 信号 | 鉴权类型 | Phase 2 构造方式 |
|---|---|---|
| `jwt.Parse` / `Bearer ` / `Authorization` header 解析 | Bearer JWT | `-H "Authorization: Bearer $TOKEN"` |
| `r.Cookie("session")` / `SetCookie` | Cookie session | `-b "session=..."` 或 `--cookie-jar` |
| `X-User-Id` header 直读(无签名校验,dev 模式) | Header-injected | `-H "X-User-Id: 1"`(最简单,优先用这条) |
| `c.GetHeader("X-API-Key")` | API key | `-H "X-API-Key: $KEY"` |
| 找不到任何鉴权 | 无鉴权 | 不加 header |

**优先找 dev-mode / mock-user 的绕过**(比如 naturehood 里的 `X-User-Id` header 和 `Jwt.MockUserID` 配置)。这个比跑真实 JWT 流程省事得多。

### 1.3 发现响应 shape

`Read` 一个典型 handler / logic 的返回代码 + 对应的 `types.go` struct:

```bash
# 看 handler / logic 文件
Grep 'httpx\.OkJson\|c\.JSON\|json\.NewEncoder' --type=go
# 看 response struct 定义
Read internal/types/types.go
```

识别:
- **plain JSON**:`httpx.OkJson(w, resp)` 直接返回 response struct → jq 过滤字段相对简单
- **envelope**:有一个外层 `{code, data, msg}` 或 `{status, payload}` 的包装 → jq 需要从 `.data` 深入
- **错误格式**:错误返回 `{error: ...}` 还是 `{code:-1, msg:...}` → diff 时要一致

### 1.4 发现本地启动方式

```bash
Read docker-compose.yml              # 或 docker-compose.dev.yml
Read Makefile                         # 找 make dev / make run
Read README.md                        # 看 "how to run locally"
Glob 'etc/*.yaml' 或 'config/*.yaml'  # 服务配置文件
```

记下:
- 依赖服务清单(MySQL / Redis / MinIO / ...)
- 服务监听 port
- DB 初始化方式(自带 migrations / 手动 init / seed 数据)

如果项目**无法本地起全栈**(比如强依赖云服务,本地起不起来),smoke 就要退化成"只跑不需要外部依赖的 endpoint"(如 `/health`,config reload 类)。或者告知用户 "本次接入无法做 smoke 回归,建议 staging 上做 canary + 回滚方案"。

---

## Phase 2:Construct(用 Write 生成定制脚本)

### 2.1 脚本放哪

**commit 到目标 repo** 的 `scripts/smoke-curl.sh`(或项目约定的 scripts 目录)。独立 commit,message 例:`test(smoke): add regression smoke for a4x-logger integration`。

### 2.2 Endpoint 选取策略

从 Phase 1.1 的 endpoint list 里挑:

- **每个 domain 至少 1 个 GET**(读路径 + DB 查询,覆盖最多 middleware + logic + repo)
- **每个 domain 能加 1 个 POST 最好加**(写路径);不能做 idempotent 就跳过
- **必选** `/health` 或类似 health endpoint(最简单的对照)
- **必选** 一个含 PII 的 endpoint(验证 IDTYPE 反射后 log 是 `[IDTYPE:...]`,但 smoke 自己 diff **API 响应** 不变;两个不同维度)
- **必选** 一个无鉴权的 endpoint(admin 直连 / og / 公开页)

**Idempotent 原则**:
- 优先 GET(读不改状态)
- POST 能用"查询接口"代替"创建接口"就代替(例:很多服务有 `/search` / `/list` 的 POST)
- 实在要验证写路径,agent **需要在脚本里 include cleanup**(DELETE 创建的资源),或者接受 smoke "只对比 status + 响应字段存在,不对比字段值"

### 2.3 脚本骨架(agent 按项目实际调整)

```bash
#!/bin/bash
# scripts/smoke-curl.sh —— a4x-logger 接入回归 smoke
# 生成时间: <timestamp>
# Agent: Claude Code
# 使用:
#   全部:           ./scripts/smoke-curl.sh
#   过滤 domain:    ./scripts/smoke-curl.sh --domain <name>

set -eu

BASE_URL="${BASE_URL:-http://localhost:8080}"

# —— Auth(根据 Phase 1.2 discover 出来的真实鉴权机制填充)——
# 示例 A: Bearer JWT
#   AUTH_HEADER="Authorization: Bearer $SMOKE_TOKEN"
# 示例 B: X-User-Id dev 模式
#   AUTH_HEADER="X-User-Id: 1"
# 示例 C: API key
#   AUTH_HEADER="X-API-Key: $SMOKE_KEY"
AUTH_HEADER="<按项目填>"

DOMAIN_FILTER="${2:-}"; [ "${1:-}" = "--domain" ] || DOMAIN_FILTER=""

hit() {
  local domain=$1 name=$2 method=$3 path=$4 data="${5:-}"
  [ -n "$DOMAIN_FILTER" ] && [ "$domain" != "$DOMAIN_FILTER" ] && return

  local resp=$(mktemp) status
  if [ "$method" = "GET" ]; then
    status=$(curl -s -o "$resp" -w "%{http_code}" -H "$AUTH_HEADER" "$BASE_URL$path")
  else
    status=$(curl -s -o "$resp" -w "%{http_code}" -H "$AUTH_HEADER" \
      -H "Content-Type: application/json" -X "$method" -d "$data" "$BASE_URL$path")
  fi

  jq -n \
    --arg d "$domain" --arg n "$name" --arg m "$method" --arg p "$path" --arg s "$status" \
    --slurpfile body "$resp" \
    '{domain:$d, name:$n, method:$m, path:$p, status:$s, body:($body[0] // null)}'

  rm -f "$resp"
}

{
  # —— Phase 1.1 discover 出的真实 endpoint(agent 填入,示意结构)——
  hit "health"  "liveness"        GET  "/health"
  hit "<dom1>"  "<name>"          GET  "<path>"
  hit "<dom1>"  "<name>"          POST "<path>" '<json>'
  hit "<dom2>"  "<name>"          GET  "<path>"
  # ...
} | jq -s '.'
```

**Agent 填空规则**:
- `AUTH_HEADER` 从 Phase 1.2 结论
- 所有 `hit "..."` 行从 Phase 1.1 的 endpoint list,按 Phase 2.2 策略选
- 如果 Phase 1.3 发现项目用 envelope 格式,脚本输出不变(是原始 body),但 Phase 4 diff filter 要调整

### 2.4 动态字段 filter

agent 在脚本旁写一个 helper(或 inline 到 diff 步骤):

```bash
# 默认 filter(覆盖大部分项目)
filter='del(.. | .trace_id?, .request_id?, .timestamp?, .span_id?, .created_at?, .updated_at?)'

# 如果 Phase 1.3 发现 envelope 格式,filter 要递归进 .data
filter='.[] | .body.data? // .body | del(.. | .trace_id?, .request_id?, .timestamp?)'

# 如果项目返回 ID 是递增的(会每次不同),再加:
filter='... | del(.. | .id?)'
```

不确定时**先跑一次 baseline,diff 第二次 baseline(对自己)**:如果 diff 出字段,那些字段都是动态的,全部加进 filter。

---

## Phase 3:Run baseline + Per-step diff(用 Bash)

### 3.1 Baseline

改第一行 SDK 相关代码前:

```bash
# 1. 起本地服务(agent 根据 Phase 1.4 结论)
docker compose up -d  # 或 make dev 或 go run ./...

# 2. 等服务 ready(agent 可以 curl /health 轮询)
until curl -sf http://localhost:8080/health; do sleep 1; done

# 3. 跑 baseline smoke
./scripts/smoke-curl.sh > /tmp/baseline-smoke.json

# 4. 跑 baseline test(按项目语言选对应命令)
go test ./... -count=1 2>&1 | tee /tmp/baseline-test.log
grep -cE '^--- FAIL|^FAIL\s' /tmp/baseline-test.log
# 记下数字,作 baseline failure count
```

**不同语言的测试命令速查**:

| 语言 | 单测命令 | 子模块测试 | 集成测试 |
|---|---|---|---|
| Go | `go test ./...` | `go test ./<pkg>` | `go test ./... -tags integration` |
| Java | `mvn -DskipITs=false verify` | `mvn -pl <module> test` | `mvn -pl <module> failsafe:integration-test` |
| Python | `pytest` | `pytest <path>` | `pytest -m integration` |

Java 项目把 `go test ./...` 全部替换为 `mvn -DskipITs=false verify`;Python 项目替换为 `pytest -x --tb=short`。
Phase 3.2 / Phase 4.3 里的"baseline test"数字对齐上表命令的输出格式即可。

**如果上述任一步失败**(服务起不来 / baseline smoke 有 HTTP 5xx):**STOP**,这说明目标项目本身在不接入 SDK 前就有问题,不是接入任务能解决的 —— 报告给用户。

### 3.2 Per-step diff 模式

每个 Step 1~8 完成后(或 Step 5 分批的每一批):

```bash
# 重新生成 after snapshot
./scripts/smoke-curl.sh > /tmp/after-step<N>-smoke.json
# 或 ./scripts/smoke-curl.sh --domain <batch> > /tmp/after-batch-<X>-smoke.json

# 过滤动态字段后 diff
jq -S "$filter" /tmp/baseline-smoke.json > /tmp/b.json
jq -S "$filter" /tmp/after-step<N>-smoke.json > /tmp/a.json
diff /tmp/b.json /tmp/a.json

# 无输出 = pass,继续
# 有输出 = STOP,agent 进入排查分支
```

### 3.3 Diff ≠ 0 时 agent 的排查顺序

1. **看 status code 是否变化** — 204 变 500?某 endpoint 本来 404 现在 200?
   - 可能:handler 注册错(路由挂了错 middleware)或改 logic 时手滑
2. **看字段值是否变化** — 某字段从 `"cloud"` 变 `""`?
   - 可能:业务 logic 返回值被改(Step 5 手滑)
3. **看字段是否新增 / 消失** — 响应多了一个字段或少了字段?
   - 可能:types.go 改 tag 时误删 json key
4. **定位到最近的 commit** — `git log --oneline <lastpass>..HEAD`,bisect 改动
5. **回滚当前 step**,修完再推进

---

## Phase 4:Commit + 交付

### 4.1 Commit smoke script

```bash
git add scripts/smoke-curl.sh
git commit -m "test(smoke): add regression smoke for a4x-logger integration"
```

这是**独立 commit**,不混在 logic 迁移或 middleware commit 里 —— reviewer 看 smoke 脚本单独评审。

### 4.2 README 挂链接

在 `README.md` 或 `CONTRIBUTING.md` 加一段:

```markdown
## Regression smoke

Run before/after significant changes to verify API contract stability:

\`\`\`bash
./scripts/smoke-curl.sh > /tmp/after.json
# compare with /tmp/baseline.json (generated before your changes)
\`\`\`

See [docs/architecture/logging/a4x-logger-migration.md] for context.
```

### 4.3 MR 描述带 baseline / final 数字

MR 描述的"本地验证"段落里,agent 要写明:

```
- master baseline:<N> test failures, <M> smoke endpoints green
- after integration:<N> test failures, <M> smoke endpoints green, diff vs baseline: 0 lines
```

具体数字由 agent 跑出来填,**不编造**(参考 memory 里的 "specific numbers must be git/grep verified" 规则)。

命令对应关系:
- Go:`go test ./... -count=1` → 看 `^--- FAIL` 计数
- Java:`mvn -DskipITs=false verify` → 看 `Tests run: X, Failures: Y, Errors: Z`
- Python:`pytest -x --tb=short` → 看 `failed` / `passed` / `error` 行

---

## 跳过 smoke 的合法场景

Agent 遇到以下情况时**可以跳过自建 smoke**,但必须**替换为等价证据**:

| 场景 | 替代 |
|---|---|
| 项目已有 Go integration test / Playwright / k6 等 E2E | 直接跑现有 E2E 抓 baseline,diff 用项目现有 harness |
| 项目已有 Postman / Insomnia collection | 用 `newman` CLI 跑 + 比对输出 |
| 项目本地无法起(强依赖云) | 放弃本地 smoke,改做"staging canary + 每改 1 批 watch 5 分钟 + 分阶段合并" |
| 项目只有个位数 endpoint | 极简 smoke:1-2 条 curl,写在 commit message 里留证,不一定开脚本文件 |

最低底限:**任何接入都不能跳过 "改动前 + 改动后的 behavior 对比"**。形式可以变,semantic 不能跳。

---

## 常见 gotcha(agent 自查)

| 陷阱 | 现象 | 对策 |
|---|---|---|
| 动态字段没过滤干净 | 每次 baseline diff 自己都有输出 | 先 `./smoke.sh > b1; sleep 1; ./smoke.sh > b2; diff b1 b2`,差异字段全部加进 filter |
| POST 不 idempotent | 第 2 次跑,response 变了(创建了第 2 条) | 改用 GET 读已存在数据;或脚本内 teardown;或只对比 status code 不对比 body |
| 鉴权 token 过期 | 全部 401 | 用 dev-mode header-injected(如果有)代替真实 JWT |
| 本地没起全依赖 | 某 endpoint 500,其他 200 | 起全 docker compose;或让那个 endpoint 脱离 smoke(标记跳过) |
| 多次跑顺序影响 | DB 里数据累积,影响 GET 列表 | 每次跑前 reset test DB;或脚本开头插入 seed 数据 |
| jq 递归过滤覆盖到业务字段 | `del(.. | .id?)` 把业务的 id 也删了,diff 对比失真 | 精确路径过滤(`.body.request_id` 而非通配 `.request_id?`)|

---

## 总结(给 agent 的 checklist)

按以下顺序执行,每项打勾才进下一项:

- [ ] Phase 1.1:endpoint list 列出(at least N domains × 2 paths)
- [ ] Phase 1.2:auth 机制确定,优先 dev-mode
- [ ] Phase 1.3:响应 shape 确定(plain / envelope / proto)
- [ ] Phase 1.4:本地起法确定,起得来服务
- [ ] Phase 2:生成 `scripts/smoke-curl.sh`,填入真实 endpoint + auth
- [ ] Phase 3.1:baseline 跑出 `/tmp/baseline-smoke.json` 和 `/tmp/baseline-test.log`
- [ ] Phase 3.1.5:对自己 diff(baseline vs baseline 再跑一次),filter 补齐动态字段
- [ ] Phase 3.2:每个 Step / batch 后跑 diff,有差异就 STOP 排查
- [ ] Phase 4:smoke script commit 进 repo,README 加 link,MR 描述带 baseline/final 数字

全做完 → 合并前 gate 自然通过。
