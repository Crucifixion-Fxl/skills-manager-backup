# 常见坑清单

已踩过的坑,按影响严重程度排序。每条给:**现象 → 根因 → 正确做法**。

## 1. Nop 静默吞日志(严重 — 线上才发现)

**现象**:前置 middleware(UserId / Tenant / Device 等)里调 `log.Warn("jwt_parse_failed", ...)`,线上**日志完全不出现**,本地也不报错。

**根因**:前置 middleware 跑在 LoggerMiddleware **之前**,此时 ctx 里还没绑 logger,`a4xlogger.FromContext(ctx)` 返回 `Nop()` —— 一个静默 logger,所有方法都是 no-op,**不报错不警告不落盘**。

```go
// ❌ 错 — 前置 middleware 里用 FromContext
func (m *UserIdMiddleware) Handle(next http.HandlerFunc) http.HandlerFunc {
    return func(w http.ResponseWriter, r *http.Request) {
        log := a4xlogger.FromContext(r.Context())   // ← 拿到 Nop()!
        log.Warn("jwt_parse_failed %v", err)        // ← 零输出
    }
}
```

**正确做法**:middleware struct 持 base logger 引用(构造时注入),Handle 里 `m.logger.WithContext(r.Context())`:

```go
// ✅ 对
type UserIdMiddleware struct {
    logger *a4xlogger.Logger   // ← 构造时传 a4xlogger.New() 返回的 base
}

func NewUserIdMiddleware(logger *a4xlogger.Logger, /* ... */) *UserIdMiddleware {
    return &UserIdMiddleware{logger: logger}
}

func (m *UserIdMiddleware) Handle(next http.HandlerFunc) http.HandlerFunc {
    return func(w http.ResponseWriter, r *http.Request) {
        log := m.logger.WithContext(r.Context())  // ✅ base + ctx
        log.Warn("jwt_parse_failed %v", err)      // ✅ 正常输出 + 自带 trace_id
    }
}
```

`WithContext(ctx)` 把 ctx 挂到 logger 上 —— SDK 在 emit 时自动从 ctx 抽 OTel SpanContext 补 `trace_id`/`span_id`(ADR-16)。

**识别信号**:如果某个 middleware 需要在 `LoggerMiddleware` 之前跑(比如 JWT 解析 middleware 在 LoggerMiddleware 之前因为后者需要 user_id),它就必须用 base logger 模式。

---

## 2. IDTYPE 反射失效(严重 — 合规过不了扫描)

**现象**:struct 上明明加了 `sensitive:"user_id"` tag,日志里的 `userId` 字段还是明文 `12345`,没包装成 `[IDTYPE:user_id:12345]`。

**根因**:业务代码自己先执行了 `fmt.Sprintf` / 字符串拼接,SDK 拿到的已经是**字符串**,反射路径走不下去。

```go
// ❌ 三种典型错误(IDTYPE 反射失效或部分失效)
log.Info(fmt.Sprintf("user %v", user))    // Sprintf 先跑,SDK 看不到 struct → 全失效
log.Info("user " + user.String())         // String() 也让 SDK 看不到 struct → 全失效
logx.Infof("user %v", user)               // logx.Xxxf printf 路径,先 Sprintf 再走 adapter → IDTYPE 全失效

// ⚠️ 业务 Logic 直接调 logx.Infow(struct 参数)— adapter 还能做 IDTYPE 反射(gozero/adapter.go convertValue),
//    但因为 logx 是全局调用无 ctx,**canonical 字段(trace_id/user_id 等)不会注入**。结果:输出和 SDK pipeline 不等价。
//    业务 Logic 应该用 a4xlogger.FromContext(ctx).Info(...) 拿 ctx-bound logger,才是双闭环正解。
logx.Infow("msg", logx.Field("user", u))
```

**正确做法**:把 `%v` 占位符留给 SDK 自己处理:

```go
// ✅ 对
log.Info("user %v", user)   // SDK 看到原始 struct → 反射 tag → 包装
```

或者用手动 API(场景 A):

```go
log.Info("user_id=%v", sensitive.FormatIDType(sensitive.UserID, uid))
```

详见 [fields-and-idtype.md](fields-and-idtype.md)。

---

## 3. `logx.SetWriter` 顺序反了 adapter 被覆盖

**现象**:main 函数里接入 SDK,跑起来框架日志(HTTP access / stat)**没变成 SDK JSON 格式**,还是 go-zero 原生格式。

**根因**:`rest.MustNewServer()` **内部会调 `logx.SetUp`**,重置 logx writer。所以 `logx.SetWriter(a4xgozero.NewWriter(logger))` 必须在 `MustNewServer` 之后调。

```go
// ❌ 错
logger := a4xlogger.New("svc", ...)
logx.SetWriter(a4xgozero.NewWriter(logger))   // ← 这行白写
server := rest.MustNewServer(c.RestConf)       // ← MustNewServer 把 writer 重置了

// ✅ 对
server := rest.MustNewServer(c.RestConf)       // ← 先
logger := a4xlogger.New("svc", ...)
logx.SetWriter(a4xgozero.NewWriter(logger))   // ← 后
```

---

## 4. gozero adapter 重复 caller(v0.2.1 已知 bug,v0.2.2 修复)

**现象**:framework access log 的 JSON 里出现 **两个** `caller` 字段:
```json
"caller":"gozero@v0.2.1/adapter.go:103",
"caller":"handler/loghandler.go:167"
```

**根因**:v0.2.1 adapter 把 go-zero 自己的 `caller` LogField 转成 zap.Field 输出,同时 SDK 的 zap auto-caller hook 也打了一个,两个共存。

**修复**:升到 v0.2.2,adapter 会自动识别 framework 传的 caller,关掉 zap auto-hook。

**临时兼容**:停留在 v0.2.1 的项目可以在 logger.yaml 里关掉 caller,或者容忍 — JSON parser(Loki / ELK)一般对重复 key 取最后一个(framework caller,`handler/loghandler.go`),行为上没坏。

---

## 5. `set -x` 让 Dockerfile 把 CI_JOB_TOKEN 回显到 build log(安全)

**现象**:Dockerfile 里 `RUN set -eux; ... git config ... $CI_JOB_TOKEN ...`,kaniko build log 里看到:
```
+ git config --global url.https://gitlab-ci-token:glpat-xxxxxxxx@gitlab.addx.ai/.insteadOf ...
```
**明文 token** 在 CI job log 里,任何有 read 权限的人都能看见。

**根因**:`-x` 是 xtrace,shell 执行每条命令前把**展开后**的形式打印到 stdout,kaniko 抓进 build log。

**正确做法**:credential-bearing RUN **不能**用 `-x`:
```dockerfile
RUN set -eu; \                                # ✅ 只 -eu,不 -x
    if [ -n "$CI_JOB_TOKEN" ]; then \
      git config --global "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf" "https://gitlab.addx.ai/"; \
    fi; \
    cd server && go mod download; \
    rm -f /root/.gitconfig                    # ✅ 同层清理,layer cache 不含 token
```

详见 [ci-docker-template.md](ci-docker-template.md)。

---

## 6. Dockerfile credential 跨 RUN 分层残留(安全)

**现象**:即使最终 alpine 镜像没包含 token,**builder 层的 cache**(kaniko `--cache-repo`)里还是有明文 `/root/.gitconfig` 包含 `gitlab-ci-token:TOKEN@`。

**根因**:
```dockerfile
RUN git config --global ... ${CI_JOB_TOKEN} ...    # 这一层产生 .gitconfig
RUN cd server && go mod download                    # 下一层用 .gitconfig
# builder 第一层文件系统 snapshot 里包含 .gitconfig → 被 push 到 cache
```

**正确做法**:同层 RUN 里 use + cleanup,确保 **这一层文件系统 snapshot 结束时** `.gitconfig` 不存在:

```dockerfile
RUN set -eu; \
    git config --global ... ${CI_JOB_TOKEN} ...; \
    cd server && go mod download; \
    rm -f /root/.gitconfig
```

`rm` 必须在**同一个 RUN** 里(不同 RUN 会变成"先存在后删除"的历史,cache 层仍包含中间状态)。

---

## 7. 路由组漏挂 LoggerMiddleware

**现象**:某几个业务路径(/api/admin/* 或 /og/* 等)的日志里**没**有 `user_id` / `request_id` / `device_msg_src`,其他路径正常。

**根因**:go-zero `.api` 文件分块声明 middleware,漏了给某几个 `@server` 块加 Logger。生成的 `routes.go` 里对应 `server.AddRoutes` 的 middleware 数组缺 Logger。

**识别**:`grep 'rest.WithMiddlewares' server/internal/handler/routes.go | grep -v Logger` 如果有输出 = 有漏。

**正确做法**:
- `.api` 文件每个 `@server` 块 `middleware:` 字段都要含 `Logger`,放最后
- `goctl` 重生成路由
- 加 CI lint(可选)检查 routes.go 所有路由组都含 `serverCtx.Logger`

**特别注意 admin / og / internal 这类没有 UserId 的路由组** —— 没有 user_id 不代表不需要 LoggerMiddleware,它们还需要 `request_id` / `trace_id` / `device_msg_src`。挂 Logger 后 user_id 缺失会自动 null-omit,不会坏。

---

## 8. 测试 fixture 漏 `AddCaller()` 导致生产 bug 测不出(本 SDK v0.2.1 教训)

**现象**:adapter 的单测都 pass,但生产有重复 `caller` 字段(见坑 #4)。

**根因**:单测 fixture 用 `zap.New(core).Named(...)` 没加 `zap.AddCaller()`,和生产 `a4xlogger.New()` 里 `zap.New(..., zap.AddCaller(), zap.AddCallerSkip(2))` 的配置不一致,生产才会打 caller hook。

**识别信号**:写 SDK adapter / wrapper 类测试时,**fixture 必须和生产 zap 配置对齐**。`AddCaller` / `AddCallerSkip` / pre-bound fields 都要匹配。

**正确做法**:测试 fixture 构造 zap 时加 `zap.AddCaller()`:

```go
// 测试 fixture 要这样
zapLogger := zap.New(core, zap.AddCaller()).Named("test-svc")
```

这是**测试可信度**的问题,不是语法 bug:缺对齐的 fixture 让测试变成"防御自己写的测试"而不是"防御生产"。

---

## 9. 业务日志冗余:message 里手动拼 user_id,字段又自动出一份

**现象**:日志看起来有重复,比如:
```json
{
  "message": "timeline_list_events result=success user_id=1144031 event_count=0",
  "user_id": 1144031,   ← 和 message 里的 user_id 重复
  ...
}
```

**根因**:业务代码手动在 message 里拼了 `user_id=%d`,而 LoggerMiddleware 已经把 `user_id` 作为独立字段注入。

**是不是 bug?** 不是。运行时正确,搜索也能搜到,只是看着冗余、日志稍大。

**建议**:迁移时顺手把业务代码里手动拼 `user_id=%d` 的部分去掉(保留字段名前缀也可以,去掉值更好):
```go
// before
log.Info("action=list_events user_id=%d event_count=%d", uid, cnt)

// after(user_id 自动字段管够,不用手动拼)
log.Info("action=list_events event_count=%d", cnt)
```

---

## 10. 分批迁移时没做 batch-level smoke → 累积 bug 到最后才暴露

**现象**:logic 迁移分了 10 批,每批只检查 "`go test` 没新增 fail",最后全迁完上 staging 后发现**某个 endpoint 响应 shape 变了**(比如某个字段消失、status code 404 变 500、order 字段名大小写改了)。回去 bisect 到某一批,修一天。

**根因**:每批迁移**只在改动范围内** `go test`,没有跨 domain 跑 smoke。手滑改了非日志代码(最常见的是改构造函数签名时连带改到了返回值 / if 分支 / error 拼接 等),单测覆盖不到就漏。

**为什么分批本身不够安全**:
- **单测是 mock 的**:构造函数签名改了,mock 也跟着改,fail 不出来
- **logic 迁移改了大量构造函数**(加 `logger *a4xlogger.Logger` 参数),每动一次签名就可能手滑改到别的东西
- **批次间的隐性耦合**:A domain 的 logic 内部调用 B domain 的 service,A 改了 B 的签名用法却没跑 B 的测试

**正确做法**:每批改完**必须** diff 业务 smoke baseline:

```bash
# 改完这批(假设是 ecosystem domain)
./scripts/smoke-curl.sh --domain ecosystem > /tmp/after-batch.json

# 过滤动态字段后 diff
jq -S 'del(.. | .trace_id?, .request_id?, .timestamp?)' /tmp/baseline-smoke.json > /tmp/b.json
jq -S 'del(.. | .trace_id?, .request_id?, .timestamp?)' /tmp/after-batch.json    > /tmp/a.json
diff /tmp/b.json /tmp/a.json
# 无输出 = OK,commit 下一批
# 有输出 = STOP,排查这一批手滑了什么
```

模板见 [regression-smoke-template.md](regression-smoke-template.md),流程见 [verification-checklist.md Step 5](verification-checklist.md)。

**识别信号**:如果你发现自己"就改几个 log 调用,肯定不会坏业务,跳过 smoke 吧"—— 这就是典型的技术债思路,**改日志的 MR 能破坏业务是反直觉但高频的事**。

---

## Cheat sheet(快速自检)

接入快 release 前过一遍:

- [ ] **Step 0 baseline 建立过**(测试失败数 + smoke snapshot)(坑 10)
- [ ] **每批迁移都跑过 batch-level smoke diff,对比 baseline**(坑 10)
- [ ] 所有前置 middleware(UserId / Tenant 等)用 `m.logger.WithContext(ctx)` 而不是 `FromContext(ctx)`(坑 1)
- [ ] 没有 `fmt.Sprintf` / `+ .String()` / `logx.Infof / Warnf / Errorf` 污染 IDTYPE 反射路径(坑 2);业务 Logic 用 `FromContext(ctx).Info` 而非 `logx.Infow`(canonical 字段才能注入)
- [ ] `logx.SetWriter` 在 `MustNewServer` **之后**(坑 3)
- [ ] SDK 版本 >= v0.2.2(坑 4)
- [ ] Dockerfile credential RUN 用 `set -eu` 不用 `-x`(坑 5)
- [ ] Dockerfile credential RUN 同层 `rm -f /root/.gitconfig`(坑 6)
- [ ] `routes.go` 每个 `AddRoutes` 都含 `serverCtx.Logger`(坑 7)
- [ ] SDK / adapter 单测 fixture 配了 `zap.AddCaller()`(如果是写 SDK 侧)(坑 8)
- [ ] 业务代码迁移后 message 没和 field 重复(坑 9,可选)
