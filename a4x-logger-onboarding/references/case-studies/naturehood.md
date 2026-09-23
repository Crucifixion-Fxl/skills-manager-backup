# Case Study: naturehood API

> **本文档是一个**具体案例**,不是 template**。你的项目架构、规模、middleware 栈几乎肯定和 naturehood 不一样 —— skill 本体(SKILL.md + 其他 references/)教的才是原则。来这里是为了看"这套原则在一个真实项目里长什么样"。

## Service Profile

| 维度 | naturehood 情况 |
|---|---|
| 语言 / 框架 | Go 1.25 + go-zero v1.10.x |
| 仓库 | `applications/naturehood` |
| 部署 | K8s + ArgoCD(staging-us / staging-eu / prod-us / prod-eu) |
| 规模 | 50 个 Logic struct,~84 处 `logx` / `logc` 调用(含 logic / middleware / provider / init 全部路径) |
| 业务 middleware | UserId(JWT 鉴权) + Metrics + CORS |
| CI | GitLab CI + kaniko |
| SDK 首次接入版本 | v0.2.1(后来升到 v0.2.2) |

## Timeline

| 时间 | 里程碑 |
|---|---|
| 2026-04-22 11:51 | 第一个接入 commit(main 入口 + logger.yaml) |
| 2026-04-22 18:52 | 第 21 个接入 commit(Sonar 抑制收尾);主接入 21 个 commits 在单日内完成 |
| 2026-04-23 03:19 UTC | MR [!250](https://gitlab.addx.ai/applications/naturehood/-/merge_requests/250) 合入 staging |
| 2026-04-23 14:44 | v0.2.2 发布后,follow-up commit 升级 |
| 2026-04-23 06:55 UTC | MR [!253](https://gitlab.addx.ai/applications/naturehood/-/merge_requests/253) 合入 staging |

**开发节奏**:主接入集中在 2026-04-22 单日(约 7 小时 21 commits),期间伴随 3 轮 AI code review 和 Sonar 反馈;次日处理 SDK v0.2.2 升级。注意这是 **AI-assisted 开发** 的节奏,不一定代表纯人工开发的合理时间范围。

## Key MRs

- **主接入 MR**:[applications/naturehood !250](https://gitlab.addx.ai/applications/naturehood/-/merge_requests/250)
- **v0.2.2 升级 follow-up**:[applications/naturehood !253](https://gitlab.addx.ai/applications/naturehood/-/merge_requests/253)
- **迁移设计文档**:[naturehood/docs/architecture/logging/a4x-logger-migration.md](https://gitlab.addx.ai/applications/naturehood/-/blob/staging/docs/architecture/logging/a4x-logger-migration.md)

## Commit 分解(21 个)

按 SKILL.md "通用接入 8 步" 的对应关系:

| step | commit | 说明 |
|---|---|---|
| 1 | `3027f2f` | `feat(server): integrate a4x-logger-sdk v0.2.1 main entry` |
| 2 | `c8a83f3` | `feat(server): add LoggerMiddleware + migrate useridMiddleware Debug log` |
| 4 | `845fc35` | `feat(server): add IDTYPE tags on user_id/device_sn fields` |
| 5 批 1 | `095c699` | ecosystem 迁移(4 Logic) |
| 5 批 2 | `bae27b1` | explore 迁移(11 Logic) |
| 5 批 3 | `959f395` | collection 迁移(5 Logic) |
| 5 批 4 | `4b9a45f` | admin 迁移(17 Logic) |
| 5 批 5 | `e0cc26d` | timeline 迁移(5 Logic) |
| 5 批 6 | `80d231d` | postcard + og 迁移(8 Logic) |
| 5 批 7 | `1b1eaf2` | provider 迁移(iot_provider.go) |
| 5 收尾 | `a526560` | videocopier 迁移 |
| 5 收尾 | `289e2a3` | init-time logs 迁移 |
| 7 | `88216b0` | 全量改 zap.Field → printf 风格 |
| 8 | `8454414` | `ci: auth gitlab.addx.ai private Go modules in test-l2/test-l3` |
| 7 | `0ae94e6` | `test(server): extract constants + loggerMiddleware unit tests` |
| 1 (fix) | `ca5e668` | `fix(server/logger.yaml): key-triggered phone/email mask patterns` |
| 8 | `6e7c6df` | `ci: auth private Go modules in lint-go + kaniko build` |
| 9 | `bab09af` | `docs(architecture/logging): a4x-logger migration design` |
| 8 (security) | `3754147` | `security(ci,docker): contain CI_JOB_TOKEN to single RUN layer` |
| 8 (security) | `8f04106` | `security(ci,docker): drop xtrace from credential RUN + static guard` |
| n/a (Sonar) | `0a0047d` | `chore(server/svc): suppress pre-existing cognitive-complexity` |

## Specific Challenges & Resolutions

下面每一条都对应 [common-pitfalls.md](../common-pitfalls.md) 里一个抽象出来的坑。这里是具体上下文。

### Challenge 1:useridMiddleware 用 `FromContext` 导致 jwt_parse_failed 日志静默丢失

**背景**:useridMiddleware 解析 JWT,如果失败想打个 Debug 日志。初始写法:
```go
logc.Debugw(ctx, "jwt_parse_failed", logx.Field("error", err))
```

迁移到 SDK 时第一反应改成:
```go
log := a4xlogger.FromContext(r.Context())
log.Debug("jwt_parse_failed %v", err)
```

**问题**:useridMiddleware 跑在 LoggerMiddleware **之前**,ctx 里没 logger,`FromContext` 返回 `Nop()`,日志完全丢失(不报错)。

**修复**(commit `c8a83f3`):
```go
type UserIdMiddleware struct {
    logger *a4xlogger.Logger   // 构造时从 ServiceContext 传入
    // ...
}

func (m *UserIdMiddleware) Handle(next http.HandlerFunc) http.HandlerFunc {
    return func(w http.ResponseWriter, r *http.Request) {
        log := m.logger.WithContext(r.Context())  // base logger + ctx
        // ...
    }
}
```

### Challenge 2:gozero adapter 重复 caller 字段(发现后推动 SDK 修)

**现象**:生产 JSON 日志里每条 framework access log 有两个 `caller`:
```json
"caller":"gozero@v0.2.1/adapter.go:103",
"caller":"handler/loghandler.go:167"
```

**影响**:JSON 重复 key,Loki / ELK parser 行为 undefined(多数取最后一个),看着别扭。

**处理**:本是 SDK bug,反推给 SDK 维护者,在 [a4x-logger-sdk MR !16](https://gitlab.addx.ai/CLOUD/a4x-logger-sdk/-/merge_requests/16) 修复,发布 v0.2.2。naturehood 通过 MR !253 升级。

### Challenge 3:Dockerfile `set -eux` 把 CI_JOB_TOKEN 回显到 build log

**背景**:初版 Dockerfile 里 credential 配置 RUN:
```dockerfile
RUN set -eux; \
    git config --global "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf" "https://gitlab.addx.ai/"; \
    ...
```

**AI code review 第三轮发现**:`-x`(xtrace)把命令展开后打印 stdout,kaniko 抓进 build log,明文 token 暴露给任何有 CI log 访问权限的人。

**修复**(commit `8f04106`):`set -eux` → `set -eu`,去掉 `-x`。额外加了静态 CI check `dockerfile-credential-check` 防回归(详见 [ci-docker-template.md](../ci-docker-template.md))。

### Challenge 4:Dockerfile credential 跨 RUN 层残留到 kaniko cache

**背景**:初版分两个 RUN,第一个 `git config`,第二个 `go mod download`。

**AI review 发现**:第一个 RUN 产生的 `/root/.gitconfig`(含明文 token)会作为 builder layer 被 kaniko push 到 `--cache-repo`,任何能读 cache registry 的人能拿 token。

**修复**(commit `3754147`):合并到同一个 RUN + 结尾 `rm -f /root/.gitconfig`,保证该 layer 文件系统 snapshot 结束时 .gitconfig 不存在。

### Challenge 5:Sonar 把 `NewServiceContext` 复杂度 27 算到本 MR 头上

**背景**:`NewServiceContext` 原复杂度就是 27,本 MR 只在函数里加了 1 行 `Logger: ... field` 赋值和 1 个 ctor 参数,没实质增加复杂度。但 Sonar 的 "new code" 判定认为我们 touch 了这个函数的行,算新 code,把 27 > 15 flag 成 critical。

**推回**:在 [MR !250 note_259642](https://gitlab.addx.ai/applications/naturehood/-/merge_requests/250#note_259642) 写评论说明"这是 pre-existing tech debt,和本 MR 范围无关",建议单独 ticket 做重构。

**Sonar 抑制**(commit `0a0047d`):加 `//NOSONAR S3776: pre-existing complexity 27>15, not introduced by this MR`。

模板见 [review-pushback-templates.md #1](../review-pushback-templates.md)。

### Challenge 6:gitlab.addx.ai private module 鉴权 — 缺 Job Token allowlist

**背景**:第一次在 naturehood 项目里引用 `gitlab.addx.ai/CLOUD/a4x-logger-sdk` 这个私有 module。CI 的 `lint-go` job 跑 `go mod download` 时:
```
fatal: unable to access 'https://gitlab.addx.ai/CLOUD/a4x-logger-sdk/go.git/': The requested URL returned error: 403
```

**解决**:
1. `applications/naturehood` project 被加入 `CLOUD/a4x-logger-sdk` 的 "Job Token Permissions" allowlist(SDK owner 操作)
2. `.gitlab-ci.yml` 里加 `GIT_CONFIG_KEY_0: "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf"` + `GIT_CONFIG_VALUE_0: "https://gitlab.addx.ai/"`
3. Dockerfile 里 `ARG CI_JOB_TOKEN` + `git config insteadOf`(见 Challenge 3/4 的后续合规要求)

commits:`8454414`(test-l2/l3)→ `6e7c6df`(lint-go + kaniko)。

## Metrics

**静态数据**:

| 指标 | 值 |
|---|---|
| 改动文件总数 | 76(git diff --stat 实测) |
| 主接入代码文件 | 50 个 Logic struct(按目录分布:admin 17 / explore 11 / postcard 6 / collection 5 / timeline 5 / ecosystem 4 / og 2) |
| 被迁移的 log 调用 | ~84 处(含 logic / middleware / provider / init 全部路径) |
| 新增文件 | 3(loggerMiddleware.go、logger.yaml、migration doc) |
| Commit 总数 | 21(主 MR !250) |
| 主接入时间跨度 | 单日(2026-04-22,约 7 小时从第 1 个到第 21 个 commit)|
| v0.2.2 升级跨度 | 次日(2026-04-23)完成 |

**review 数据**:

| 指标 | 值 |
|---|---|
| AI code review 轮次 | 3 轮 |
| Sonar issue(本 MR 引入) | 0(推回 + `//NOSONAR` 抑制) |
| Sonar issue(pre-existing,算在 MR 头上) | 1(`NewServiceContext` 复杂度 27,用 `//NOSONAR S3776` 抑制) |

> **注意**:上述时间指标反映的是 **AI-assisted 开发节奏**(Claude Code 参与脚手架 + 分批迁移),不代表纯人工开发的合理时间。你的项目如果是人工接入,实际时长会更长。

## Links to Abstract Principles

本 case study 中的每个 Challenge 都对应 skill 里的抽象原则:

| Challenge | Skill 原则 |
|---|---|
| 1. useridMiddleware 静默吞日志 | [common-pitfalls.md #1 Nop 静默吞日志](../common-pitfalls.md) |
| 2. 重复 caller | [common-pitfalls.md #4 gozero adapter 重复 caller](../common-pitfalls.md) |
| 3. set -x token 回显 | [common-pitfalls.md #5 / ci-docker-template.md](../common-pitfalls.md) |
| 4. 跨层 credential 残留 | [common-pitfalls.md #6 / ci-docker-template.md](../common-pitfalls.md) |
| 5. Sonar pre-existing 复杂度 | [review-pushback-templates.md #1](../review-pushback-templates.md) |
| 6. Job Token allowlist | [ci-docker-template.md](../ci-docker-template.md) |
| 7. Job Token allowlist | [ci-docker-template.md](../ci-docker-template.md) |

## 不适用 naturehood 的情况

你的项目如果满足以下任何一条,**直接套 naturehood 的做法很可能翻车**:

- **不是 Go / 不是 go-zero** → 完全不同的 framework integration pattern,看 SKILL.md 的 decision tree 选别的 playbook
- **规模差异大**:小服务(10 个以内 Logic)不用分批迁移,一个 commit 搞定即可;naturehood 分了 10 个子 step(step 3.1 ~ 3.10,按 domain 分)是因为 50 个 Logic 需要控制 diff 大小;超大服务(500+ Logic)需要更复杂的分阶段策略
- **middleware 栈不同**:naturehood 只有 UserId + Metrics,你的服务如果有 Tenant / Session / Device 等需要额外绑 canonical 字段
- **CI 不是 GitLab + kaniko**:[ci-docker-template.md](../ci-docker-template.md) 的模板需要重新适配
- **不用 Sonar**:推回模板 [review-pushback-templates.md](../review-pushback-templates.md) 的 Sonar 相关几条用不上

当你的项目差异点出现在某个 Challenge 里 naturehood 没遇到的情况,别硬套 —— 去 SKILL.md 找抽象原则,case study 仅仅是一个参考。
