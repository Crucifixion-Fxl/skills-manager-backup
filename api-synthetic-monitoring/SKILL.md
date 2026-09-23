---
name: api-synthetic-monitoring
description: 为已部署服务搭建 API synthetic monitoring（主动巡检） — Python pytest + GitLab CI 定时流水线 + 飞书签名 webhook 告警 + ReportPortal 结果聚合。两个核心价值：(1) 持续健康度巡检即时发现故障，MTTD 从"用户投诉"缩到分钟级；(2) staging/prod 发布后几分钟内 smoke 检测部署问题（S3 读不到、凭证漂移、DB migration 被静默跳过、上游契约变化），避免 App / 设备 / QA 团队联调时浪费时间。当用户需要检测部署间隙 staging/prod API 的静默故障、捕获依赖服务（DB / 上游 / 第三方）悄悄失效、按固定节奏验证接口契约、或搭建比 K8s probe 更深的部署后验证时使用。也适用于用户提到"synthetic monitoring"、"主动巡检"、"API 巡检"、"health monitoring"、"active probe"、"smoke test"（口语化用法）、"部署后验证"、"发布后验证"、"联调前自检"，或已有 K8s probe 但仍被静默故障偷袭的场景。沉淀了 3 个最难诊断的坑（ci-templates stage 契约、protected 分支变量注入、Git Flow 绕过），帮你少花几小时排障。
---

# API Synthetic Monitoring（主动巡检）

## Description

用 Python pytest + GitLab CI 定时流水线对已部署服务做**主动 HTTP 探针**（synthetic monitoring）。失败发签名飞书卡，结果汇总到 ReportPortal。**与 K8s probe 和被动可观测性正交** — 填"进程活着"（K8s）和"用户已经踩坑"（Prometheus/Sentry）之间的那段盲区。

> **术语说明**：Synthetic monitoring 是行业标准说法（Datadog / Google SRE），强调**主动合成请求**对抗真实用户流量的被动观测。中文用"主动巡检"最贴切。口语里偶尔说"smoke test"指代类似动作，但严格意义 smoke test 是构建后一次性检查，本 skill 是**持续、周期性**的，不是 smoke。
>
> **实现级命名**保留 `smoke/` 目录、`smoke:patrol` CI job —— 业内缩写惯用、参考实现（customer-care 项目）已经这么部署，改名收益低。

## 价值

两个核心价值 — 把故障从"被用户发现"压到"被巡检发现"：

### 1. 发布后 smoke 检测 — 避免跨团队联调时间黑洞

staging / prod 发布完成 → 巡检在 2-5 分钟内跑一轮 → **在 App / 设备 / QA 工程师开机之前**告警本次部署引入的问题：

- S3 / MinIO / OCI 凭证漂移，导致服务读不到资源
- Vault / ExternalSecret / ConfigMap 注入失败，导致某路径缺 env 变量 500
- DB migration 文件名不合规被 golang-migrate 静默跳过 → 线上 SQL 报 `Unknown column`
- 上游服务（CMS / iot-service / auth）接口契约变了，下游反序列化静默丢字段
- 网关/APISIX 路由配置漂移（staging 路由对，prod 路由没同步）

**没有巡检的代价**：App 工程师接到"我这边登录失败 / 看不到图片"→ 找后端查 log → 才发现是 S3 凭证还没从 Vault 注进来。一次联调被这类低级故障打断，通常 30 分钟到几小时的定位 + 跨团队沟通成本。

**有巡检的代价**：飞书告警 → 运维直接修配置 → 联调启动时一切正常，App / 设备 / QA 拿到干净环境。

> 在 GitLab CI 里把巡检 job 挂在 `deploy-*` 之后作为 post-deploy gate：部署绿 + 巡检绿 才允许 release channel 推给下游团队。把"联调失败的前置"堵在部署侧，不是联调侧。

### 2. 持续健康度巡检 — MTTD 从"用户投诉"压到分钟级

K8s probe 只管进程活；Prometheus / Sentry 在用户真的踩坑后才有数据。中间盲区：

- S3 凭证 2 小时前轮换失败，服务暂时还靠缓存不触发 5xx → cache 过期后才爆
- GrowthBook token 凌晨 3 点失效，业务只是 feature flag 拿不到值不 crash → 第二天才有人反馈
- 上游 CMS 返回的 JSON 多了个 null 字段，下游序列化静默截断 → 产品说"数据不完整"但没人报
- 某条 feed API 只在**有数据的租户**才走到某个代码路径，另一个租户的数据一直默默缺失

巡检**每 5 分钟主动构造一次真实请求并断言返回内容**，故障发生后 ≤ 5 分钟告警进群，**MTTD 从天级 / 小时级 → 分钟级**，让 oncall 在用户感知之前介入。

## 什么时候用

K8s liveness/readiness 只告诉你进程还在。Prometheus/Sentry 告诉你真实用户已经受伤了。两者都抓不住：

- 部署间隙上游依赖悄悄挂了（S3 凭证轮换、GrowthBook token 过期、CMS 返回脏数据）
- 接口响应层面的契约回归（字段丢失、shape 变了）
- 配置灰度跨 DC 漂移（A 区 v5、B 区还在 v4）
- 部署后比 curl 深、比 E2E 轻的主动验证
- **新功能上线后，联调人员（App / 嵌入式 / QA）需要一个"环境已经就绪"的确认信号**，而不是第一次跑 E2E 才发现凭据没注进来

Synthetic monitoring 填这段空白：**小的、持续的、主动的 HTTP 探针，失败即告警**。

## Rules

1. **每个探针必须有明确 severity**（P1 阻断 / P2 警告）。没 severity = 没人 triage。
2. **URL 进代码（`envs.py`），凭据进 CI Variables**。混用会让新环境上手成本暴涨。
3. **CI Variables 必须 protected + masked**（endpoint 类可不 mask 但必须 protected）。非 protected 分支拿不到 protected 变量，这是特性不是 bug。
4. **`.gitlab-ci.yml` stages 必须包含 `test`**。`engineering/ci-templates` 强制注入 `ci:init` 在 `test` stage，缺失会导致 pipeline 0 jobs + failed + 无 yaml_errors（最难诊断的失败模式）。
5. **巡检代码跟随项目 Git Flow，不走捷径**。feat 直接合 master 会让 staging/intercept 长期缺失代码 → 后续同步 MR 出现假删除 diff。
6. **红路径必须验证**：故意打坏一个 URL，确认飞书卡真的到群里。没验证过的告警通道 = 可能的静默故障。
7. **v1 探针数 ≤ 3 项**。每个探针都得能回答"这个挂了我要不要 page 人"，否则就是噪音。
8. **飞书 webhook 必须用签名版**（HMAC-SHA256）。公司 bot 默认开签名校验，plain webhook 会被拒绝。
9. **本地可跑**：`python -m pytest --env=staging-us` 在没有 CI variables 的情况下也要能跑（secrets 缺失时 Feishu/RP 静默跳过，但测试本身得跑）。

## Non-Goals

- 不替代 K8s probe（probe 保留）
- 不替代完整 E2E / UAT（那些留在对应的测试层）
- 不做压测 / 性能测试
- 不替代系统级可观测性（Prometheus / Sentry / Snowplow）

## 架构（参考实现）

```
┌─────────────────────────────────────────────────────────┐
│           GitLab CI 定时流水线 (*/5min)                   │
│                                                          │
│   smoke:patrol (parallel matrix × N env)                 │
│   ┌──────────┐ ┌──────────┐ ┌──────────────┐            │
│   │ Health   │ │ Contract │ │ Rules/Version│            │
│   │ Check    │ │ Probe    │ │ Freshness    │            │
│   └──────────┘ └──────────┘ └──────────────┘            │
│                      │                                   │
│              ┌───────┴────────┐                          │
│              │ pytest_session │                          │
│              │    finish      │                          │
│              └───┬───────┬────┘                          │
│          ┌───────┘       └──────────┐                    │
│   ┌──────▼──┐              ┌────────▼─────┐              │
│   │ 飞书    │              │ ReportPortal │              │
│   │ (失败)  │              │ (所有 run)   │              │
│   └─────────┘              └──────────────┘              │
└─────────────────────────────────────────────────────────┘
```

## 工作流

按顺序走，即使用户说"直接给我代码"也别跳步 — 每一步都对应一个需要用户拍板的决策。

### Step 1: 划定探针范围

写代码之前，先对齐"什么叫巡检挂了"。让用户填：

| 探针 | 请求 | 通过条件 | 严重度 |
|------|------|---------|--------|
| Health | `GET /health` 或等效 | 哪个状态码 + body shape = "活着"？ | P1 |
| Contract | 一个核心生产接口 | 响应里哪些字段必须存在？ | P1 |
| Freshness / version | `/metrics` 或版本字段 | 什么算"过期"？（如 1h 没更新过 version） | P2 |

v1 每个服务 ≤ 3 项。越多 = 噪音越大、triage 越难。深度断言留到后续迭代。

**经验法则**：第一版只测"挂了真的会 page 人"的东西。其他都是 scope creep。

### Step 2: 确定配置策略

两桶分开：

- **URL / 环境元数据** → 代码（`smoke/envs.py`）、checked in、PR review。见 [assets/envs.py.template](assets/envs.py.template)。
- **凭据** → GitLab CI Variables、`protected + masked`。绝不进代码。

**为什么 URL 不放 env var？** Ingress / 公开端点本来不是 secret。放代码里有 audit trail、diff review、本地零配置跑（`pytest --env=staging-us` 开箱即用）。env var 机制留给真正的 secret。

### Step 3: 初始化项目

从 [assets/](assets/) 模板复制出目录：

```
smoke/
├── pyproject.toml          # from pyproject.toml.template
├── .gitignore              # __pycache__/, *.egg-info/, .pytest_cache/
├── envs.py                 # from envs.py.template — URL 的 SSOT
├── config.py               # from config.py.template — 加载器
├── conftest.py             # from conftest.py.template — pytest fixture + 飞书 hook
├── checks/
│   ├── __init__.py
│   ├── test_health.py
│   ├── test_contract.py
│   └── test_freshness.py
└── lib/
    ├── __init__.py
    └── feishu.py           # from feishu.py.template — 签名 Card v2
```

不要从零写这些文件，模板里沉淀了非显而易见的决策（飞书签名算法、pytest hook 顺序、port discovery 的回退逻辑）。

详细分步走见 [references/setup-guide.md](references/setup-guide.md)。

### Step 4: 理解 ci-templates 契约（**最关键的坑**）

AddX 项目大多把 `ci_config_path` 指向 `engineering/ci-templates:main/.gitlab-ci/entrypoint.yml`，而不是本地 `.gitlab-ci.yml`。模板会透明 include 项目的 `.gitlab-ci.yml`，但**同时注入强制的 anchor job**：

- `ci:init` 在 stage `test`
- `global:credentials-scan` + `global:repo-boundary-lint` 在 stage `.pre`
- `global:code-review` 在 stage `.post`（**仅 MR 事件且目标分支 protected**）

`global:sonarqube-scan` **不在默认入口**——只有准入名单里的项目才被切到 `entrypoint-sonar.yml`。
完整的注入清单、触发条件与 `global:code-review` 的三档（强制门禁／warn-only／黑名单）
是 [references/ci-templates-contract.md](references/ci-templates-contract.md) 的那张表，**全仓以它为准**。

**坑**：如果项目 `.gitlab-ci.yml` 写 `stages: [smoke]` 没带 `test`，GitLab 会拒绝 pipeline，状态 `failed`、`jobs=[]`、`yaml_errors=null`。**极难诊断** — 看起来像幻觉失败。

**修复**：stages 始终包含 `test`。巡检 job 放 `stage: test`（跟 `ci:init` 并行互不影响）。

详见 [references/ci-templates-contract.md](references/ci-templates-contract.md)。

### Step 5: 加 CI job

从 [assets/gitlab-ci-monitor.yml.template](assets/gitlab-ci-monitor.yml.template) 复制巡检 job，追加到项目 `.gitlab-ci.yml`。关键触发：

- `$CI_PIPELINE_SOURCE == "schedule"` — cron（主路径）
- `$CI_COMMIT_BRANCH == "staging"` + changes filter — 部署后
- `$CI_PIPELINE_SOURCE == "merge_request_event"` + changes filter — MR 预检
- `$CI_PIPELINE_SOURCE == "web" or "api"` — 运维手动

**changes filter**（`changes: [smoke/**, .gitlab-ci.yml]`）防止巡检 job 在无关 MR 里刷屏。

### Step 6: 配 CI 变量

| Variable | Masked | Protected | 说明 |
|----------|--------|-----------|------|
| `FEISHU_WEBHOOK_URL` | ✅ | ✅ | Bot webhook URL |
| `FEISHU_WEBHOOK_SECRET` | ✅ | ✅ | 签名 bot 的 secret |
| `SMOKE_RP_ENDPOINT` | ❌ | ✅ | RP URL（如 `https://reportportal.builder.addx.live`） |
| `SMOKE_RP_PROJECT` | 可选 | ✅ | RP project 名 |
| `SMOKE_RP_TOKEN` | ✅ | ✅ | RP API token |

**为什么 protected**：承载凭证。Protected = 只在 protected branch / tag 的 pipeline 注入。

**为什么会咬你**：如果在 feature 分支测试，变量不注入，飞书通知静默 no-op，你会以为代码坏了。详见 [references/protected-branches.md](references/protected-branches.md)。

**验证期变通**：临时把 feature 分支 protect 起来（API 一行命令），验完再 unprotect。

### Step 7: 建定时流水线

```bash
curl -s -X POST -H "PRIVATE-TOKEN: $TOKEN" \
  --data-urlencode "description=<service> API Monitoring" \
  --data-urlencode "ref=master" \
  --data-urlencode "cron=*/5 * * * *" \
  --data-urlencode "cron_timezone=UTC" \
  --data-urlencode "active=true" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/pipeline_schedules"
```

或 UI：project → CI/CD → Schedules → New Schedule。

**节奏**：默认 5min。更紧 = 更 flaky、噪音更多。更松 = 反应慢。除非真的需要，别小于 2min。

### Step 8: 端到端验证

两条路径都看到才算数：

1. **绿路径** — 手动触发一次，确认所有 check 都 pass，RP 里能看到新 Launch
2. **红路径** — 故意把 `envs.py` 里的一个 URL 改坏（末尾加 `-DOES-NOT-EXIST`），push，触发，**确认飞书群收到卡**。在 protected 分支做，保证变量注入。
3. 改回去，再验证绿。

**红路径不能跳过**。一个静默失败的巡检比没有巡检更糟 — 它给你假信心。

### Step 9: 写服务专属的 runbook

在目标项目建 `docs/deployment/api-monitoring.html`，覆盖：
- 日常操作：怎么手动触发、怎么查历史、怎么看 RP
- 加新环境：在 `envs.py` 的一行变更
- 本服务特有的故障模式
- 挂了 page 谁

骨架内容参考 [assets/runbook.md.template](assets/runbook.md.template)，但目标项目产物必须写成 HTML authoring SSOT。

## 关键设计决策（ADR）

### 为什么用 pytest？

不用 bash、不用 curl 脚本。pytest 给你：
- **结构化失败数据** — nodeid + message + traceback，直接喂进飞书卡，不用解析日志
- **Skip 机制** — 干净处理"依赖还没部署"（`pytest.skip`）
- **插件生态** — `pytest-reportportal` 一行接入 RP
- **本地可跑** — 开发者 `pytest --env=staging` 直接跑，不依赖 CI

### 为什么飞书放 `pytest_sessionfinish`，不放 CI `after_script`？

Hook 在 pytest 进程里跑，所以：
- 失败详情已经在内存里（不用从日志解析）
- 可以用 Python 构造完整签名 payload
- 本地和 CI 走同一条代码路径

`after_script` 也行，但要你再从日志文件里 grep 失败信息。

### 为什么用签名 webhook，不用 plain？

公司飞书 bot 通常默认启签名校验（组织安全策略）。plain webhook 会被拒绝。`timestamp + "\n" + secret` HMAC-SHA256 算法在 [assets/feishu.py.template](assets/feishu.py.template)。

### 为什么 URL 放代码，不放 CI Variable？

- Ingress URL 不是 secret
- Code diff 在 review 里能看到新环境变更
- 本地跑 smoke 零额外配置
- 加 matrix 是一行代码，不是点 UI

CI Variable 留给真正的 secret。

## Git Flow：会让你浪费 30 分钟的陷阱

**故事**：巡检代码在 `feat/` 分支搞完，想"这就是个巡检，无关紧要"，直接开 MR 到 master。CI 过了，合了，schedule 跑起来，皆大欢喜 —— 一天。

然后下一个 feature 走项目标准 Git Flow（`feat → intercept → staging → master`），而巡检跳过了这条路，**staging 和 intercept 从来没拿到巡检代码**。之后任何 `intercept → staging` 同步 MR 都会显示"删除 master 有但 intercept 没有的文件"。diff 让人摸不着头脑，review 浪费时间。

**规则**：基础设施变更也要走项目 Git Flow，即使是"微不足道"的变更。项目用 `feat → intercept → staging → master` 就都走这条路。是的，更慢，但值得。

如果已经踩坑了，见 [references/git-flow-recovery.md](references/git-flow-recovery.md) 的回溯合并流程。

## 委派给其他 skill

| 子任务 | 委派到 |
|--------|-------|
| 创建 / 管理 GitLab MR | `gitlab-mr` |
| 写 / review `.gitlab-ci.yml` | `gitlab-ci` |
| 为剩余 P1/P2 建跟踪 issue | `gitlab-issue-sop` |
| 超出 smoke 的可观测性策略（SLA、漏斗） | `observability-design` |
| 飞书卡片消息设计 | `reply-feishu` |

## Examples

### ❌ Bad — 自定义 `stages` 破坏 ci-templates 契约

```yaml
# 症状: pipeline status=failed, jobs=[], yaml_errors=null
# 原因: engineering/ci-templates 注入的 ci:init 需要 test stage
stages:
  - smoke           # 自创 stage，没 test

smoke:patrol:
  stage: smoke
  # ... 永远跑不起来
```

### ✅ Good — 复用 `test` stage，与 `ci:init` 并行

```yaml
stages:
  - test            # 必须包含，anchor for ci:init

smoke:patrol:
  stage: test       # 与 ci:init 并行，互不干扰
  # ...
```

---

### ❌ Bad — URL 丢进 CI Variables

```
# GitLab Settings → CI/CD → Variables
SMOKE_ADMIN_URL_STAGING_US = "https://admin-staging.addx.live"
SMOKE_ADMIN_URL_PROD_US    = "https://admin.addx.live"
SMOKE_ADMIN_URL_PROD_EU    = "https://admin-eu.addx.live"
SMOKE_ADMIN_URL_PROD_CN    = "https://admin-cn.addx.live"
# ... 每加一个环境要点一次 UI，没 diff 审查，本地跑 pytest 还得 export 一堆 env
```

### ✅ Good — URL 在代码里（`smoke/envs.py`）

```python
# smoke/envs.py — SSOT, checked in, PR-reviewed
ENVIRONMENTS = {
    "staging-us": {"admin_url": "https://admin-staging.addx.live"},
    "prod-us":    {"admin_url": "https://admin.addx.live"},
    "prod-eu":    {"admin_url": "https://admin-eu.addx.live"},
    "prod-cn":    {"admin_url": "https://admin-cn.addx.live"},
}
```

CI Variable 留给真正的 secret：`FEISHU_WEBHOOK_URL`、`FEISHU_WEBHOOK_SECRET`、`SMOKE_RP_TOKEN`。

---

### ❌ Bad — 在 feat 分支测飞书，误判代码坏了

```
# 开发者在 feat/api-monitor 上跑 pipeline，故意打坏一个测试
# 看到：test_admin_health FAILED
# 但是：飞书群没收到卡
# 结论："飞书代码坏了" ← 误诊
# 实际：feat 分支非 protected，FEISHU_WEBHOOK_URL 根本没注入
```

### ✅ Good — conftest.py 打 debug 日志暴露真相

```python
# conftest.py
def pytest_sessionfinish(session, exitstatus):
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL")
    # 这一行 CI 日志直接告诉你是变量问题还是代码问题
    print(f"[smoke notify] webhook_url_set={bool(webhook_url)}")
    if not webhook_url:
        print("[smoke notify] skipping — 可能不在 protected branch 上")
        return
    # ...
```

看到 `webhook_url_set=False` → 临时 protect feat 分支做验证，不要去 debug 飞书代码。

---

### ❌ Bad — feat 直接 MR 到 master

```
feat/api-monitor → master  (直接合)
# 之后某天：
staging 没巡检代码，有人开 intercept → staging MR，发现 diff 里显示删除 smoke/ 50 个文件
# 原因：巡检绕过 git flow，master 有、staging/intercept 没有
```

### ✅ Good — 跟随项目 Git Flow

```
feat/api-monitor → feature/kb_edge_feature_intercept → staging → master
# 每一步都有 CI 验证、code review、部署阶段 burn-in
# staging 那步部署后巡检自动覆盖 staging env，master 上的巡检覆盖 prod env
```

## 验收清单

报"完成"之前：

- [ ] 绿路径 pipeline 端到端验证通过（test → RP launch）
- [ ] 红路径 pipeline 验证通过（打坏 URL → 飞书卡到达正确的群）
- [ ] CI 变量都是 protected + masked
- [ ] Schedule active，`next_run_at` 在将来
- [ ] `docs/deployment/api-monitoring.html` 写好（服务专属 runbook）
- [ ] Launch checklist 标清 P0 已完成项和 P1/P2 阻塞项
- [ ] 在已有阻塞 issue 上补了 follow-up comment
