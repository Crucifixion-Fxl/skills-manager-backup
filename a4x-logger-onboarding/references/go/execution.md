# Execution — 接入流程纪律

> 本文件教 **agent 怎么按流程接入 + 怎么保证业务无回归**。Phase 0 Recon 通过后走本文件。
>
> 配套文件:
> - SDK API 用法 → [sdk-usage.md](sdk-usage.md)
> - 项目探查 → [project-recon.md](project-recon.md)
> - 新写日志规约 → [log-style-guide.md](log-style-guide.md)(soft,migration 不强制)
> - 通用坑 → [common-pitfalls.md](common-pitfalls.md)

---

## 1. Phase 1 11 步骨架

按下表执行,**每行右侧适用性看 §2 表,机械跑全 11 步会做无用功**。

| # | 步骤 | 变动量 | 可独立上线 |
|---|---|---|---|
| 0 | **Pre-flight baseline** —— Agent 自己 discover endpoint / auth / 响应 shape,生成 `scripts/smoke-curl.sh` 跑 baseline + `go test` 记失败数。详见 [regression-smoke-template.md](regression-smoke-template.md) | 1 个 smoke 脚本 | n/a |
| 1 | **主入口初始化 SDK** —— `a4xlogger.New(...)` + `logx.SetWriter(NewWriter(logger))`。模板见 [sdk-usage.md §3](sdk-usage.md) | main 文件 + logger.yaml | ✅ |
| 2 | **写 LoggerMiddleware** —— 收集 canonical 字段绑到 logger,写回 ctx。模板见 [sdk-usage.md §4](sdk-usage.md) | 新文件 ~50 行 | ✅ |
| 3 | **路由覆盖** —— 所有路由组都挂 Logger middleware。发现命令见 [project-recon.md §3.2](project-recon.md) | N 个路由注册点 | ✅ |
| 4 | **types 加 IDTYPE tag** —— Request/Response struct 的 PII 字段。发现命令:`Grep -nE '\b(UserID\|userId\|uid\|Email\|email\|Mac\|MacAddr\|SerialNo\|SerialNumber\|TicketID\|UserSn)\b' --type=go <project-types-path>`,逐字段对照 [fields-and-idtype.md](fields-and-idtype.md) PII 变体表加 `sensitive:"<idtype>"` | M 个 struct | ✅ 静态改 |
| 5 | **现有日志迁移** —— 按 §3 **机械替换 4 规则**;大型项目分批见 §4 | 业务代码,可分批 | ✅ 按目录逐批 |
| 6 | **前置 middleware 改 base logger 模式** —— 任何在 LoggerMiddleware **之前**跑的 middleware 不能用 `FromContext`,要 struct 持 base + `WithContext`(见 [sdk-usage.md §2](sdk-usage.md)+ [common-pitfalls.md #1](common-pitfalls.md))。**如果现有 middleware 是裸函数 wrapper**(`func Recovery(next http.HandlerFunc) http.HandlerFunc`)而非 struct,先做小重构:加 struct + 构造函数持 `base *a4xlogger.Logger`,在 ServiceContext / server 初始化处传入 | 少数文件 | ⚠️ |
| 7 | **测试日志捕获** —— 已有 buf-based capture → `testutil.NewLogCapture` 替换。发现:`Grep 'bytes\.Buffer.*logx\|bytes\.Buffer.*log\.SetOutput' --type=go` | 相关 test 文件 | ✅ |
| 8 | **CI/Docker 私有 module 鉴权** —— `CI_JOB_TOKEN` + `git config insteadOf` + 同层 `rm gitconfig`。见 [ci-docker-template.md](ci-docker-template.md) | `.gitlab-ci.yml` + `Dockerfile` | ✅ |
| 9 | **写设计文档** —— 已有日志迁移:migration doc;全新接入:setup doc。AI code-review 会卡"代码有文档无"。模板见 §6 | 1 份 md | ✅ |
| 10 | **(全新接入 only)生成 demo domain** —— 1 Logic + test + types.go IDTYPE 示例 + 交付总结。已有日志迁移项目跳过(有真实 logic 可参考) | demo 文件 + 总结 | ✅ |

---

## 2. 按模式查 Step 适用性

Recon 探到的"项目现状"决定每步的实际工作量。**Agent 不要机械跑全 11 步**:

| Step | 全新接入 | 已有日志迁移 |
|---|---|---|
| 0 baseline | ⚠️ 只跑 `go test` 记失败数;**跳过 smoke discover**(项目没业务可 smoke) | ✅ 完整(test + smoke) |
| 1 SDK init | ✅ | ✅ |
| 2 LoggerMiddleware | ✅(即使 auth 没接,middleware 先就位,user_id 块留 TODO 注释) | ✅ |
| 3 路由覆盖 | ✅ 工作量小(0-1 路由) | ✅ |
| 4 types IDTYPE tag | ➖ 跳过(没现成 type)→ Step 10 demo 内示范 | ✅ |
| 5 日志迁移 | ➖ 跳过(没现成调用)→ Step 10 demo 内示范 | ✅ **主战场**,见 §3-4 |
| 6 前置 middleware | ➖ 通常无前置 mw / ✅ 有就改 | ✅(取决于现有 mw) |
| 7 测试日志捕获 | ➖ 跳过 → Step 10 demo 内示范 | ✅ |
| 8 CI/Docker 鉴权 | ⚠️ 项目无 CI → 跳过 + 交付总结 flag | ✅ |
| 9 设计文档 | ✅ setup doc(简短) | ✅ migration doc |
| 10 demo domain | ✅ **必做** | ➖ 跳过 |

**符号**:✅ 必做 / ⚠️ 视情况调整 / ➖ 跳过。

---

## 3. Step 5 — 机械替换 4 规则(**任意源统一 recipe**)

**核心立场**:migration 不是重构。目标是把日志走 SDK 管线(拿 PII mask / trace 关联 / canonical 字段),**不是顺手把现有日志全部规约化**。强行规约化 = agent 理解每条业务语义 + 用户 review 争论命名 + 14 小时命名决策 —— 代价远超 SDK 接入本身。

### 4 条规则

| 规则 | 含义 |
|---|---|
| **① API 必换** | `log.Xxx` / `logx.Xxx` / `logger.Xxx`(自研 wrapper)→ 对应 SDK logger 调用。这是 SDK 接入的硬要求,不换 = migration 白做 |
| **② message 文本基本保留** | **不发明 action 名,不强加 `result=`,不重命名变量为 canonical 名**(如 `uid` 不改 `user_id`)。原 message 里啥样,换完基本啥样 |
| **③ inline 动态值可以套 `key=%v`**(可选)| 原 `log.Printf("user %d done", uid)` 可改成 `l.logger.Info("user done user_id=%v", uid)` —— 给 grep / 字段提取友好,**但不强制** |
| **④ 特殊 SDK 硬要求** | • `log.Fatal*` 拆成 `logger.Errorf(...) + os.Exit(1)`(SDK 禁 Fatal,见 [sdk-usage.md §8](sdk-usage.md))<br>• `error` 类型当最后参数(SDK 自动抽到 `exception` 字段)<br>• **pre-SDK-init 例外**:`a4xlogger.New(...)` 调用**之前**的 `log.Fatal*` / `log.Print*`(启动期 bootstrap 阶段)**保留 stdlib log**,这一阶段 SDK logger 还没构造,不能用。agent 加注释 `// pre-SDK-init: stdlib log intentionally; cannot use a4xlogger before .New()` |

### 取 logger 的 3 种源(决定 ① 里的"对应 SDK logger"是什么)

| 调用点 | logger 从哪来 |
|---|---|
| Logic struct / HTTP handler(有 `ctx` / `r.Context()`,LoggerMiddleware 之后)| `a4xlogger.FromContext(ctx)` |
| Middleware Handle 内 / 前置 middleware(LoggerMiddleware 之前)| middleware struct 持 `base *a4xlogger.Logger`,用 `m.logger.WithContext(r.Context())` |
| main / bootstrap / startup hooks(无 ctx)| `main` 里 `a4xlogger.New()` 的返回值,直接用 |

### Practical 注意:goimports 对私有 module 失效

改完 `log.Xxx` → `l.logger.Xxx` / `a4xlogger.FromContext(...)` 等,**首次 `go build` 前必须手动在文件头补 import**:

```go
import (
    a4xlogger "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go"
    "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go/sensitive"        // 用 FormatIDType / Wrap 时
    "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go/sensitive/testutil" // 测试文件用 NewLogCapture 时
)
```

**为什么**:`goimports` / VS Code / Cursor 自动 import 扫不到私有 GitLab(`gitlab.addx.ai`),不会自动补进来,`go build` 会报 `undefined: a4xlogger`。agent 每新编辑一个文件后**先手动补 import**,再跑 build。

### 4 个典型例子

```go
// 例 1: 业务 logic 里
log.Printf("user %d done", uid)
→ l.logger.Info("user done user_id=%v", uid)
// ① API 换;③ 动态值 uid 套 key=%v(可选,也可保留 "user %v done", uid)
```

```go
// 例 2: 启动事件(无动态值)
log.Println("renewal worker: starting")
→ l.logger.Info("renewal worker: starting")
// ① 只换 API,文本一字不动
```

```go
// 例 3: 启动失败 Fatal
log.Fatalf("config: %v", err)
→ logger.Errorf("config: %v", err)
os.Exit(1)
// ① API 换;④ Fatal 拆成 Error+Exit;logger 是 main 里的 base
```

```go
// 例 4: Recovery middleware panic
log.Printf("[PANIC] %s %s: %v\n%s", method, path, err, stack)
→ m.logger.WithContext(r.Context()).Error("[PANIC] %s %s: %v\n%s", method, path, err, stack)
// ① API 换 + 取 base+WithContext(recovery 是前置 mw);message 模板一字不动
```

### 不该做的

- ❌ 不要给每个 `log.Printf("user %d done", uid)` 强行起 `action=user_done` — 162 条要一条条想 = 14 小时纯命名
- ❌ 不要把 `log.Println("cache warmed")` 硬转 `Info("cache_warmed result=success")` — `result=success` 凑字
- ❌ 不要把原 `uid` 改成 `user_id` 变量名 —— 那是变量重构,不是日志迁移
- ❌ 不要在 migration 时套 [log-style-guide.md](log-style-guide.md) 的 `action=/result=` convention — 那是**新写日志推荐**,不是迁移强制

---

## 4. 大型项目分批策略

### 4.1 分批节奏

| 规模 | 建议 |
|---|---|
| < 20 个调用点 | 1-2 个 commit 搞定,没必要分批 |
| 20-100 个调用点 | 5-10 批,按 domain 分 |
| > 100 个调用点 | 按 domain 分;**单个 domain > 30 个调用点**时继续按子目录细分(如 `internal/order/cancel/` / `order/list/`),避免一批 commit 超大 reviewer 看不动 |
| > 500 个调用点 | 可考虑分多个 MR 上线 |

### 4.2 工作量粗估

| 场景 | Agent 实测 | 用户视角 wall time |
|---|---|---|
| 已有日志迁移 | 分钟级 IO(MultiEdit 一遍扫一遍改),瓶颈是 7 项验证跑测试的 wall time | 小/中项目 2-3 人天;大型 5-10 人天(主要花在 review + 业务回归排查) |
| 全新接入 | Step 5 跳过,只算 Step 1+2+10 demo | 半人天内 |

> **(未来工程化方向)codemod / AST 工具**:目前 agent MultiEdit 分钟级就跑完,不内置 codemod。等出现 >500 调用且多项目排队时再立项单独工具。

### 4.3 Pilot 批

铺开前先挑一个小 domain 做 pilot(5-10 个调用点)验流程,再按模式推。

### 4.4 Step 5 开始前的 pre-flight grep(避免 Step 7 意外被打破)

**Step 5 改 API 前先扫一次**,列出哪些测试文件靠 `log.SetOutput(&buf)` / `bytes.Buffer` 做日志捕获 —— 这些文件的断言会因本次 API 替换**自动失效**(原 `log.Printf` 不再往 `&buf` 写),Step 5 测试会红。

```bash
Grep -l 'log\.SetOutput\|bytes\.Buffer' --type=go | xargs grep -l '_test\.go\|t\.Run\|testing\.T'
```

把命中文件**列成 TODO**,标 "Step 7 要改成 `testutil.NewLogCapture`"。然后:

- **选项 A(推荐)**:Step 5 开跑前先把这批文件的旧断言**注释掉**(加 `// TODO step 7: migrate to testutil.NewLogCapture`),Step 5 迁移时测试不会因它们意外红;Step 7 再统一重写为 SDK 风格断言
- **选项 B**:Step 5 照常跑,遇到 test 红就 drop 去 Step 7,处理完再回 Step 5

A 更干净(测试红 / 不红的原因清晰),B 灵活(对小项目成本低)。按项目规模选。

### 4.4 每批迁移 7 项强制验证

**Agent 自己跑,不 delegate**。改完一批 → 跑 7 项全过 → commit → 下一批。任何一项 fail,STOP 修或 revert,不累积。

| # | 动作 | 命令 |
|---|---|---|
| 1 | **SDK 接入验证:本批非 SDK 调用清零** | `Grep 'log\.\(Printf\|Println\|Fatal\|Print\|Panic\)\|logx\.\(Info\|Error\|Warn\|Debug\)' --type=go <batch-dir>` 应**无输出**(排除注释) |
| 2 | **build 过** | `Bash: go vet ./... && go build ./...` |
| 3 | **测试回归**:失败数 ≤ Step 0 baseline | `Bash: go test ./... -count=1` → 数 fail → 对比 `/tmp/baseline-test.log` |
| 4 | **PII 兜底扫描**:本批裸标量 PII 已包成 IDTYPE | 跑 [pii-scanner.md](pii-scanner.md),本批扫描 0 hit 才过 |
| 5 | **Smoke 回归**:本批 domain 的 endpoint diff baseline 无差异 | `./scripts/smoke-curl.sh --domain <batch> > /tmp/after.json` → jq 过滤动态字段 → diff baseline |
| 6 | **本批 commit message 含 step 标号** | 例如 `feat(<domain>): migrate logs (step 3.<N>/<total>)` |
| 7 | **Logic struct / 构造函数改了 → 调用方同步** | `Grep 'New<Struct>Logic\(' --type=go` 所有调用点确认编译过 |

**如果 #1 失败**:本批还有漏改的 API → grep 找出来全换完(任意源统一,不分 stdlib / logx,同一 recipe)
**如果 #2 失败**:构造函数签名改动碰到测试 fixture / 调用点 → 修 fixture / 补默认值
**如果 #3 / #5 失败**:不要硬推下一批。常见原因:手滑改了非日志代码(if 条件 / 返回值)/ 误改了业务 struct

### 4.5 收尾批次

主业务 logic 迁完后,**非业务路径**要单独处理:
- `internal/provider/*` / 外调 / service-to-service
- `main.go` / init-time / bootstrap hooks
- worker / asynq / kafka consumer

这些调用点**没 ctx-bound logger 可用**,按 §3 的"取 logger 的 3 种源"里的 (b)/(c) 类走 base logger 模式。参考 [sdk-usage.md §2](sdk-usage.md)。

---

## 5. 合并前 E2E gate

**5 项全过才发 MR**(已有日志迁移):

| # | gate |
|---|---|
| ① | smoke diff baseline 零差异 |
| ② | 测试失败数 ≤ Step 0 baseline |
| ③ | **全仓非 SDK 调用清零**:`Grep 'logx\.(Info\|Error\|Warn\|Debug)\|log\.(Printf\|Println\|Fatal\|Print\|Panic)' --type=go` 排除 vendor + `_test.go` 注释,**0 命中**(per-batch #1 不等于全局,此为全局 gate) |
| ④ | **canonical 字段无双注入**:覆盖**所有** `.api` / `routes.go` 里声明的 endpoint — agent 先 grep / Read 列出全集,确认 `scripts/smoke-curl.sh` 100% 覆盖;跑 smoke + tail 服务日志,把所有 JSON 喂 jq:`jq -e 'select(.user_id \| type == "array" or .request_id \| type == "array" or .tenant_id \| type == "array")' /tmp/smoke-logs.json` 应**无输出**(`filterReservedFields` 失效会让 canonical 字段变 array 而非 scalar) |
| ⑤ | **PII 反射闭环**(覆盖所有 endpoint):smoke 跑完后 `grep '\[IDTYPE:' /tmp/smoke-logs.json` 必须命中至少 1 次(全 0 = struct tag 全没生效;部分 endpoint 有 / 部分没 = 缺那批漏加 tag 或 idtype 拼错) |

**全新接入 gate**(简化 — 无 smoke baseline):
- 测试失败数 ≤ Step 0 baseline + demo 新增 test 全绿(`testutil.NewLogCapture` 已能验证日志生成);可选:起服务 `curl` 一下 demo endpoint 肉眼对照 [log-style-guide.md §1](log-style-guide.md) pattern

---

## 6. 设计文档模板

### 6.1 Migration doc(已有日志迁移用)

路径:`docs/architecture/logging/a4x-logger-migration.md`

```markdown
# A4X Logger SDK 迁移设计

| 文档状态 | YYYY-MM-DD |
| 对应 MR | !<number> |
| SDK 版本 | v0.2.2 |
| 作用范围 | `<directory>/` |

## 1. 背景与目标
  1.1 现状(迁移前):日志实现 + ~N 个调用点 + 当前 PII 风险
  1.2 目标(迁移后):5 点能力(结构化 JSON / PII mask / canonical 字段 / IDTYPE 反射 / trace 关联)
  1.3 Non-goals:明确不做什么(避免 scope creep)

## 2. 迁移范围
  2.1 代码改动(文件级)
  2.2 配置改动(logger.yaml / CI / Dockerfile)
  2.3 测试改动

## 3. 字段契约
  3.1 Canonical 字段(LoggerMiddleware 注入)
  3.2 IDTYPE 反射标签
  3.3 PII mask(logger.yaml)

## 4. 回滚策略
  4.1 完全回滚(git revert)
  4.2 降级到 log_raw(ConfigMap flip)
  4.3 框架原生日志回退(删 SetWriter 一行)

## 5. 与现有文档的差异
  5.1 与 observability.md 的关系
  5.2 与 SDK 仓库 spec/ 的关系

## 6. 可追溯链接
  - MR
  - SDK repo
  - 相关代码 entry point
```

在 `docs/architecture/overview.md` 或 README 加一行 link 指向本文档(AI reviewer "可追溯链接" 检查要用)。

### 6.2 Setup doc(全新接入用)

路径:`docs/architecture/logging/a4x-logger-setup.md`

全新项目没"迁移决策"可写,setup doc 比 migration doc 短一半,只描述**新搭的日志体系长什么样**:

```markdown
# A4X Logger SDK 接入设计 — <service-name>

| 文档状态 | YYYY-MM-DD |
| 对应 MR | !<number> |
| SDK 版本 | v0.2.2 |

## 1. 接入目标
- 全新服务 day 1 用统一日志 SDK
- 5 点能力(结构化 JSON / PII mask / canonical 字段 / IDTYPE 反射 / trace 关联)

## 2. 实装范围
  2.1 SDK 初始化:`<main-file>` + `etc/logger.yaml`
  2.2 LoggerMiddleware:`<middleware-path>/loggerMiddleware.go`
  2.3 路由覆盖:`<routes-file>` 的 <N> 个路由组
  2.4 示例 demo:`<logic-dir>/example/`(可删,见交付总结)

## 3. 字段契约
  3.1 Canonical 字段(LoggerMiddleware 注入):request_id / device_msg_src(✅ 已注入);user_id(⚠️ 待 auth 接入后补)
  3.2 IDTYPE 反射标签:仅 demo `ExampleGetUserRequest` 已示范
  3.3 PII mask(logger.yaml):预置规则,业务出新 PII 模式时按 [fields-and-idtype.md](../../../skills/a4x-logger-onboarding/references/fields-and-idtype.md) 扩

## 4. 待办(后续业务铺开时补)
  4.1 接入 auth 后:LoggerMiddleware 里 user_id 注入解开 TODO
  4.2 业务 endpoint 稳定后:补 smoke 回归脚本
  4.3 CI 立项后:按 [ci-docker-template.md](ci-docker-template.md) 配 `CI_JOB_TOKEN`

## 5. 可追溯链接
  - MR / SDK repo / skill 链接
```

---

## 7. Step 10 — Greenfield Demo(全新接入 only)

agent 跑完 Step 1-9 后项目搭好了基础设施,用户可能不知道怎么写第一个业务 Logic。生成一个 demo 给用户照抄。

### 7.1 Demo 文件(3 个)

创建命名明显的示例 domain(推荐 `example/` / `hello/`,避免和真实业务 domain 冲突):

**`<types-file>`** — 加 Request/Response struct + IDTYPE tag 示例:

```go
// ExampleGetUserRequest 是 a4x-logger-onboarding skill 生成的示例。
// 演示:PII 字段打 sensitive tag,SDK 反射做 IDTYPE 包装。
type ExampleGetUserRequest struct {
    UserID int64 `json:"userId" path:"userId" sensitive:"user_id"`
}

type ExampleGetUserResponse struct {
    UserID   int64  `json:"userId" sensitive:"user_id"`
    Nickname string `json:"nickname"`   // 非 PII,不打 tag
}
```

**`<logic-dir>/example/getUserLogic.go`** — 两条典型 log 路径:

```go
// Package example 是 a4x-logger-onboarding skill 生成的示例 domain。
// 展示接入 a4x-logger-sdk 后业务 Logic 怎么写日志。
// 规约见 references/log-style-guide.md。写完自己的真实 Logic 可以 rm 整个 example/。
package example

func (l *GetUserLogic) GetUser(req *types.ExampleGetUserRequest) (*types.ExampleGetUserResponse, error) {
    user, err := l.queryUserByID(req.UserID)

    if err == nil {
        l.logger.Info("example_get_user result=success user_id=%v", req.UserID)
        return &types.ExampleGetUserResponse{UserID: user.ID, Nickname: user.Nickname}, nil
    }

    if errors.Is(err, sql.ErrNoRows) {
        l.logger.Warn("example_get_user result=not_found user_id=%v", req.UserID)
        return nil, err
    }

    l.logger.Error("example_get_user result=failed user_id=%v", req.UserID, err)
    return nil, err
}
```

**`<logic-dir>/example/getUserLogic_test.go`** — `testutil.NewLogCapture` 断言模板:

```go
func TestGetUserLogsNotFound(t *testing.T) {
    cap := testutil.NewLogCapture(t, sensitive.NewEmptyConfig())
    ctx := a4xlogger.WithLogger(context.Background(), a4xlogger.NewFromZap(cap.Logger))

    l := example.NewGetUserLogic(ctx, testSvcCtx)
    _, err := l.GetUser(&types.ExampleGetUserRequest{UserID: 999999})

    last := cap.Last(t)
    msg, _ := last["message"].(string)
    if !strings.Contains(msg, "example_get_user") {
        t.Fatalf("expected action=example_get_user, got:\n%s", msg)
    }
}
```

**独立 commit**:`feat(example): add demo domain showcasing a4x-logger usage patterns`

文件头注释**明确标**"skill 生成的 demo,写完自己 Logic 可删"。

### 7.2 交付总结(agent 通过 text 输出给用户)

Step 10 结束时 agent 在对话里给用户这段 structured summary。占位符替换成真实值,数字 git/grep 实测,**不编**。

```markdown
## a4x-logger 接入完成 — <service-name>

### 我搭好的基础设施

只列**实际做了**的项;跳过的项移到下方"未做项 / TODO"。

| 能力 | 产出物 |
|---|---|
| SDK 初始化 | `<main-file>` L<N>:`a4xlogger.New(...)` + `logx.SetWriter(NewWriter)` |
| 配置文件 | `<etc-dir>/logger.yaml`(PII mask 规则已预置) |
| LoggerMiddleware | `<middleware-path>/loggerMiddleware.go` |
| 路由覆盖 | `<routes-file>`:<N> 个路由组都挂了 Logger middleware |
| Setup 文档 | `docs/architecture/logging/a4x-logger-setup.md` |

### 未做项 / TODO(全新接入常见)

| 项 | 为什么没做 | 后续怎么补 |
|---|---|---|
| CI 鉴权配置 | 项目当前无 `.gitlab-ci.yml` | CI 立项时按 [ci-docker-template.md](ci-docker-template.md) 加 `CI_JOB_TOKEN` |
| `user_id` 字段注入 | 项目当前无 auth(JWT/session) | 接入 auth 后,LoggerMiddleware 里 user_id 注入块的 TODO 注释解开 |
| Smoke 回归脚本 | 项目暂无业务 endpoint 可 smoke | 业务接口稳定后按 [regression-smoke-template.md](regression-smoke-template.md) 补 |

### 我生成的示例代码(参考用,可删)

| 文件 | 看什么 |
|---|---|
| `<types-file>` — `ExampleGetUserRequest/Response` | struct IDTYPE tag 打法 |
| `<logic-dir>/example/getUserLogic.go` | success / not_found / error 三种模式 |
| `<logic-dir>/example/getUserLogic_test.go` | `testutil.NewLogCapture` 断言 |

示例文件头标了"skill 生成的 demo",删法:`rm -r <logic-dir>/example/` + 从 types/routes 清引用。

### 你写新业务 Logic 的 3 步

1. struct 加 `logger *a4xlogger.Logger` 字段 + 构造函数 `logger: a4xlogger.FromContext(ctx)`
2. 业务日志按 `action=<name> result=<state> <fields>` pattern(见 [log-style-guide.md](log-style-guide.md))
3. types.go 中 PII 字段加 `sensitive:"<idtype>"`(见 [fields-and-idtype.md](fields-and-idtype.md))

### Baseline 数字(git 实测,不编)

| 指标 | 值 |
|---|---|
| Test failures (pre-integration) | <N> |
| Demo test 新增 | +<K>(全绿) |

> 全新接入无 smoke baseline(Step 0 跳过了 smoke discover)。业务接口稳定后按 [regression-smoke-template.md](regression-smoke-template.md) 补。

### MR
<MR link>
```

---

## 8. MR 描述模板(已有日志迁移用)

```markdown
## Summary
- 迁移 <service> 的 <N> 处日志调用到 a4x-logger-sdk v0.2.2
- 加 LoggerMiddleware 注入 <fields> canonical 字段
- types IDTYPE tag 覆盖 <M> 个 PII 字段
- CI/Docker 配私库鉴权

## Baseline 数字(git/grep 实测)
- Pre-integration test failures: <N>
- Logx/log 调用总数: <count>
- 分批: <B> 批 commit(step 3.1 / 3.2 / ...)

## Final 数字
- Post-integration test failures: <N>(未劣化)
- 全仓 `log\.|logx\.` 命中: 0
- Smoke diff baseline: 0 diff
- PII 反射闭环: `[IDTYPE:` 命中 ✓

## 合并前 5 项 gate
- ✅ ① smoke diff = 0
- ✅ ② test ≤ baseline
- ✅ ③ 全仓非 SDK 调用清零
- ✅ ④ canonical 字段无双注入(覆盖所有 endpoint)
- ✅ ⑤ PII 反射闭环(覆盖所有 endpoint)

## 相关
- 迁移设计文档:docs/architecture/logging/a4x-logger-migration.md
- SDK version: go/v0.2.2
```

---

## 9. 典型 commit 序列

```
1. test(smoke): add regression smoke for a4x-logger integration     (Step 0)
2. feat(server): init a4x-logger SDK + logger.yaml                   (Step 1)
3. feat(middleware): add LoggerMiddleware injecting canonical fields (Step 2)
4. feat(routes): mount Logger middleware on all route groups         (Step 3)
5. feat(types): add sensitive tag to PII fields                      (Step 4)
6. feat(<domain>): migrate logs to a4x-logger (step 5.1/5.<total>)   (Step 5 batch 1)
7. feat(<domain>): migrate logs to a4x-logger (step 5.2/5.<total>)   (Step 5 batch 2)
... N 个批次 ...
N+7. feat(middleware): refactor <pre-mw> to base+WithContext          (Step 6)
N+8. test: replace buf-based log capture with testutil.NewLogCapture  (Step 7)
N+9. ci(docker): add CI_JOB_TOKEN auth for private module            (Step 8)
N+10. docs(architecture): add a4x-logger migration design             (Step 9)
```

每个 commit 独立 review 友好 + 可独立 revert。
