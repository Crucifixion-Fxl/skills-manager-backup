# 验证清单

每步完成后按对应段落跑验证。**不跑验证就继续下一步 = 隐藏 bug 到后面叠加起来难排查**。

接入的大原则:**每一步改造完都要同时证明两件事**:
1. 本步骤的 **SDK 接入功能** 已生效(logger_init / 字段注入 / IDTYPE 渲染 等)
2. 本步骤没有 **破坏业务功能**(测试通过率不下降 + 核心路径 smoke 响应不变)

缺任何一件就 STOP,排查再往下。

---

## Step 0:Pre-flight baseline snapshot(**必跑,否则后续对比无锚**)

接入**一行代码都没改之前**,先跑 baseline。所有后续对比都以此为锚。

**这一步由 Agent 自己跑,不是让用户做。** Agent 按 [regression-smoke-template.md](regression-smoke-template.md) 的 4 phase 执行:Discover(用 Grep/Read 探目标项目)→ Construct(用 Write 生成 smoke 脚本)→ Run baseline → Commit 脚本进 repo。

### 0.1 Discover + Construct smoke 脚本

Agent 完成:
- [ ] `Grep` / `Read` 出目标项目真实 endpoint list(from routes.go / .api / swagger)
- [ ] 确定 auth 机制(Bearer / Cookie / X-User-Id dev mode / 无鉴权)
- [ ] 确定响应 shape(plain JSON / envelope)
- [ ] 确定本地起服务方式(docker compose / make dev / go run)
- [ ] `Write` 生成 `scripts/smoke-curl.sh`,填入真实 endpoint + auth
- [ ] 脚本独立 commit:`test(smoke): add regression smoke for a4x-logger integration`

### 0.2 跑 baseline(Agent 用 Bash)

```bash
# 1. 起本地服务(agent 按 Discover 阶段结论)
docker compose up -d   # 或 make dev 或 go run ./...
until curl -sf http://localhost:8080/health; do sleep 1; done

# 2. 跑 smoke baseline
./scripts/smoke-curl.sh > /tmp/baseline-smoke.json

# 3. 自 diff:对自己跑两次,识别动态字段
./scripts/smoke-curl.sh > /tmp/baseline-smoke-2.json
diff /tmp/baseline-smoke.json /tmp/baseline-smoke-2.json
# 有差异字段全部加进 jq filter(trace_id / request_id / timestamp / 等)

# 4. 跑 test baseline(按语言选命令,见下表)
```

**Step 0 baseline — 按语言选测试命令**:

| 语言 | baseline 命令 | 记失败数方式 |
|---|---|---|
| Go | `go test ./... -count=1 2>&1 \| tee /tmp/baseline-test.log` | `grep -cE '^--- FAIL\|^FAIL\s' /tmp/baseline-test.log` |
| Java | `mvn -DskipITs=false verify 2>&1 \| tee /tmp/baseline-test.log` | `grep -E 'Tests run:.*Failures:' /tmp/baseline-test.log` |
| Python | `pytest -x --tb=short 2>&1 \| tee /tmp/baseline-test.log` | `grep -E 'passed\|failed\|error' /tmp/baseline-test.log` |

记下失败数字,假设 Go 是 37 / Java 是 `Failures: 2, Errors: 0` / Python 是 `3 failed`。

### 0.3 Baseline 数字写进 MR TODO

Agent 在 MR 描述 / 接入过程中的 TODO 记录(不编数字,**直接从 grep/diff 输出贴**):
```
baseline test failures: 37 (pre-existing from master, not introduced by this MR)
baseline smoke endpoints: N endpoints, all <status> (列实际)
jq filter 用: <agent 最终确定的 filter 表达式>
```

### Step 0 验证

- [ ] `scripts/smoke-curl.sh` 已生成 + commit
- [ ] `/tmp/baseline-test.log` 生成,失败数已记录
- [ ] `/tmp/baseline-smoke.json` 生成,自 diff 已跑通(filter 修到对自己 diff = 0)
- [ ] baseline 数字记在 MR TODO / 接入 notebook

**如果 baseline 是 0 failures** → 接下来任何一批引入新失败都 blocker。
**如果 baseline 是 37 failures**(有 pre-existing fail)→ 接下来每批保持 ≤37,增了就 blocker。

### 跳过 Step 0 的合法场景

参考 [regression-smoke-template.md §跳过 smoke 的合法场景](regression-smoke-template.md):
- 项目已有 Go integration test / Playwright / Postman collection → agent 直接跑现有工具,输出当 baseline
- 项目本地起不来(强依赖云) → agent 退化到 staging canary(合并后灰度 + watch)
- 项目只有个位数 endpoint → 极简 smoke(1-2 条 inline curl,不必开脚本)

**但不能完全跳过"改动前 vs 改动后对比"的 semantic**。Agent 跳过必须给出替代证据 + 在 MR 描述里说明。

---

## Step 1:主入口 SDK 初始化

**build 验证(按语言)**:

| 语言 | build 命令 | 期望 |
|---|---|---|
| Go | `go build ./...` | 0 编译错误 |
| Java | `mvn -pl <module> compile -q` | BUILD SUCCESS |
| Python | `python -c "import <app-module>"` / `ruff check .` | 0 import error / 0 lint error |

- [ ] build 通过
- [ ] 启动服务,第一条输出是 `logger_init` JSON:
  ```bash
  # Go
  go run . -f etc/<service>.yaml 2>&1 | head -3
  # Java:起服务后 curl /actuator/health 看 stdout 里首条 JSON
  # Python:uvicorn main:app → 看 stdout 首条 JSON
  # 期望:{"level":"INFO","message":"logger_init","service":"<name>",...}
  ```
- [ ] 配一条 k8s probe(`/health`),curl 一次,framework access log 是 SDK JSON 格式
- [ ] **业务无回归**:

| 语言 | 命令 | 要求 |
|---|---|---|
| Go | `go test ./... -count=1` | 失败数 ≤ Step 0 baseline |
| Java | `mvn -DskipITs=false verify` | Failures + Errors ≤ baseline |
| Python | `pytest -x --tb=short` | failed ≤ baseline |

## Step 2:Middleware / Filter(LoggerMiddleware / LoggingContextFilter / ASGI Middleware)

- [ ] middleware 测试全绿:
  - Go:`go test ./internal/middleware/... -run LoggerMiddleware` — 6 个 case 全绿
  - Java:`mvn -pl <module> test -Dtest=*LoggingContextFilter*` — Filter 单测全绿
  - Python:`pytest tests/test_middleware.py -v` — middleware 测试全绿
- [ ] 测试覆盖:inject user_id / omit user_id / request_id 透传或自生成 / device_msg_src / logger/MDC/contextvars retrievable
- [ ] **业务无回归**:

| 语言 | 命令 | 要求 |
|---|---|---|
| Go | `go test ./... -count=1` | 失败数 ≤ Step 0 baseline |
| Java | `mvn -DskipITs=false verify` | Failures + Errors ≤ baseline |
| Python | `pytest -x --tb=short` | failed ≤ baseline |

## Step 3:路由覆盖

- [ ] `grep 'rest.WithMiddlewares' server/internal/handler/routes.go | grep -v Logger` 无输出(所有路由组都挂了)
- [ ] 起服务 + curl 任意 `/api/**` 路径,log JSON 里有 `user_id` / `request_id` / `device_msg_src`
- [ ] curl admin / og 等路由(没 UserId),log JSON 里有 `request_id` / `device_msg_src`,`user_id` 缺失(null-omit)
- [ ] **业务无回归**:重新跑 smoke snapshot,diff 两份:
  ```bash
  ./scripts/smoke-curl.sh > /tmp/after-step3-smoke.json
  diff /tmp/baseline-smoke.json /tmp/after-step3-smoke.json
  # 应该:status code / 响应体 schema / 关键字段值 都无变化
  # (time/trace_id 这类每次都不同的字段需要 jq 过滤掉再 diff)
  ```

## Step 4:IDTYPE tag

- [ ] `go build ./...` 通过(tag 写错不会 build fail,但加 tag 时手误可能打错 struct 定义)
- [ ] `grep -rn "UserID\|userId\|Email\|SerialNumber" server/internal/types/types.go | grep -v sensitive` 无输出(或每条都有合理理由,如 Response 里只返回不接收)
- [ ] curl 触发一个含 PII 字段的 endpoint,log message 里 PII 字段应该是 `[IDTYPE:<type>:<value>]` 形式
- [ ] **业务无回归**:smoke snapshot diff 无变化(tag 是 log-side 反射,不影响 API 响应)

## Step 5:logic 迁移(**最容易出问题的步骤,每批都要完整验证**)

**分批迁移时,每批都要独立跑完以下验证再进下一批**。任何一项失败 → 不要继续下一批,先修或 revert 当前批。

每批(一个 domain / 一个目录)改完后:

### 5a. SDK 接入正确性
- [ ] `go vet ./...` 无 printf 格式错误
- [ ] `grep -rn "logx\.Info\|logx\.Error\|logx\.Warn\|logx\.Debug" internal/logic/<batch-dir>/` 无输出(本批残留 0 个 logx 调用)

### 5b. 业务无回归 — 测试维度
- [ ] `go test ./... -count=1` 失败数 **≤** Step 0 baseline
  - **如果失败数增加** → 这一批破坏了某个现有测试,不要继续,找出哪个 test case 新坏的(`diff /tmp/baseline-test.log /tmp/after-batch-X-test.log`)
- [ ] **特别留意** 本批 domain 的测试:`go test ./internal/logic/<batch-dir>/... -count=1` 全绿

### 5c. 业务无回归 — smoke 维度
- [ ] 针对本批 domain 的 endpoint 跑 smoke,diff baseline:
  ```bash
  # 只跑这一批相关的 curl
  ./scripts/smoke-curl.sh --domain <batch-domain> > /tmp/after-batch-X-smoke.json
  jq -S 'del(.. | .trace_id?, .request_id?, .timestamp?)' /tmp/baseline-smoke.json > /tmp/b.json
  jq -S 'del(.. | .trace_id?, .request_id?, .timestamp?)' /tmp/after-batch-X-smoke.json > /tmp/a.json
  diff /tmp/b.json /tmp/a.json
  # 应该无差异
  ```
- [ ] 如有差异:先 inspect,判断是预期改动(几乎不应该 — 迁 log 不改业务)还是破坏(stop + 回滚这批)

### 5d. 全量 commit 前的收尾
全部批迁完后:
- [ ] 全仓 `grep -rn "logx\.Info\|logx\.Error\|logx\.Warn\|logx\.Debug\|logc\.Info\|logc\.Error" internal/` 只有 main.go / init 代码(非业务路径)
- [ ] 全量 smoke diff baseline 无差异

## Step 6:前置 middleware

- [ ] 每个前置 middleware(UserId / Tenant / Device)用 `m.logger.WithContext(r.Context())`,**不**用 `FromContext`(grep 确认)
- [ ] middleware 里故意触发错误(比如塞错 JWT),log 应该出现,**不是静默吞**
- [ ] **业务无回归**:smoke 重跑对比 baseline

## Step 7:测试改写

- [ ] `grep -rn "logx.SetWriter" internal/` 只在 main.go(SDK adapter)出现,不在测试文件
- [ ] 所有日志断言测试用 `testutil.NewLogCapture` + `a4xlogger.NewFromZap`
- [ ] `go test ./... -count=1` 失败数 ≤ Step 0 baseline
  - **注意**:测试改写后可能会有**测试本身 fail**(断言写错或 expectation 不对),这类是真的要修,不是 pre-existing
  - 诊断方法:`diff /tmp/baseline-test.log /tmp/after-step7-test.log` 看新增失败的 test name,对应修

## Step 8:CI/Docker

- [ ] CI 上 `lint-go` / `test-*` job 全绿(不再 `authentication required`)
- [ ] `docker build` 本地跑过 / kaniko CI build 成功
- [ ] `dockerfile-credential-check` job 绿(见 [ci-docker-template.md](ci-docker-template.md))
- [ ] 最终镜像不含 `~/.gitconfig`:
  ```bash
  docker run --rm <image-tag> cat /root/.gitconfig 2>/dev/null
  # 期望:空输出 + exit 非 0
  ```

## Step 9:迁移文档

- [ ] `docs/architecture/logging/<service>-a4x-logger-migration.md` 存在
- [ ] 至少包含 6 个必要章节:背景/范围/字段契约/回滚/与现有 observability.md 差异/可追溯链接
- [ ] `docs/architecture/overview.md` 或项目根 README 有 link 指向上述文档

## 合并前 E2E 契约回归(**强制,作 gate**)

Step 1-9 全做完 + PII 扫描做完后,发 MR 之前跑一遍完整回归。

### 10.1 本地完整起服务

```bash
# 起所有真实依赖(MySQL / Redis / MinIO / 等,按项目约定)
docker compose up -d
# 等待 DB ready
# 跑 migrations
# 起服务
go run . -f etc/<service>.yaml
```

### 10.2 跑完整 smoke + diff baseline

```bash
./scripts/smoke-curl.sh > /tmp/final-smoke.json

# 过滤动态字段后做全量 diff
jq -S 'del(.. | .trace_id?, .request_id?, .timestamp?, .span_id?)' /tmp/baseline-smoke.json > /tmp/b.json
jq -S 'del(.. | .trace_id?, .request_id?, .timestamp?, .span_id?)' /tmp/final-smoke.json > /tmp/f.json
diff /tmp/b.json /tmp/f.json
# 期望:无 diff(API contract 零变化)
```

### 10.3 关键用户旅程手工验证

挑 **1-2 个核心业务旅程** 手工跑通(比如:登录 → 查列表 → 创建一条记录 → 再查列表看到)。目的:
- 验证 middleware / logic / DB 链路端到端正常
- 日志里字段链完整(同一 trace_id 贯穿多个请求,user_id 一致)

### 10.4 全量测试

按语言选对应命令:

| 语言 | 命令 | 要求 |
|---|---|---|
| Go | `go test ./... -count=1 2>&1 \| tee /tmp/final-test.log` | 失败数 ≤ Step 0 baseline |
| Java | `mvn -DskipITs=false verify 2>&1 \| tee /tmp/final-test.log` | Failures + Errors ≤ baseline |
| Python | `pytest 2>&1 \| tee /tmp/final-test.log` | failed ≤ baseline |

```bash
# 对比 baseline 看哪些 test 新增 / 消失
diff /tmp/baseline-test.log /tmp/final-test.log
```

### gate 规则

| 条件 | 行动 |
|---|---|
| smoke diff 无差异 + test failures ≤ baseline | ✅ 可以发 MR |
| smoke diff 有差异 | ❌ stop,排查哪一步引入(可能是 Step 6 前置 middleware 改了错) |
| test failures > baseline | ❌ stop,`diff` 看新增 fail,修到 ≤ baseline |
| 关键用户旅程跑不通 | ❌ stop,查中间件 / logic 改动 |

## PII 扫描(独立于 9 步)

见 [pii-scanner.md](pii-scanner.md)。全部迁移完成 + 合并前 E2E 做完之后跑,独立一个 commit。

- [ ] 扫描命令跑完,产出 hit list
- [ ] 每条 hit 分类:struct field / 裸标量 / 不确定
- [ ] 裸标量全部改写成 `sensitive.FormatIDType(...)`
- [ ] 不确定的加 TODO 注释
- [ ] `go build` + `go test` 通过
- [ ] 单独 commit
- [ ] 重新跑 10.2 smoke + 10.4 test,依然 ≤ baseline

## 发 MR 前最后一遍

- [ ] Step 0 baseline + 每步后的 baseline 对比数据记录在 MR 描述(e.g. "baseline 37 fails, final 37 fails, smoke diff clean")
- [ ] 所有 commit 消息符合 conventional commits 格式
- [ ] MR 描述按 [execution.md §8 MR 描述模板](execution.md) 填
- [ ] 迁移设计文档已加 link 到架构 overview
- [ ] 准备好 [review-pushback-templates.md](review-pushback-templates.md) 里的常见回复模板

## 为什么要这么啰嗦地保护业务

本 skill 覆盖的是"在运行的生产服务上迁移日志基础设施"—— 一个 MR 里改几十上百个文件,很容易:
- 在 Step 5 某一批里手滑改了业务逻辑(不只是 log 调用,还误改了 if 条件 / 返回值)
- 在 Step 6 改 middleware 时改坏了 ctx 传递
- logic 构造函数签名改动碰到测试 fixture 没更新

这些改动**编译会过、单测可能过、但 smoke 会差 1 个字段**。没有**每步业务回归**,你会在 staging 环境跑了一天才发现某个接口的响应 shape 变了。

所以:**保护 > 效率**。每步 30 秒的 smoke,比累积到 staging 回滚 2 天好。
