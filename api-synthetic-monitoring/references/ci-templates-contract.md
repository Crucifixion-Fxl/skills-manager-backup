# ci-templates 契约 — `test` stage 坑详解

## 症状

你 push 分支，GitLab 建了 pipeline，然后：

- **Status**：`failed`
- **Jobs**：`[]`（空）
- **yaml_errors**：`null`
- **duration**：`null`，`started_at == finished_at == created_at`

你盯着 `.gitlab-ci.yml` 看，一切正常。`glab api ci/lint` 也说 `"valid": true`。Pipeline 就是被拒绝，没有错误信息。

## 根因

许多 AddX 项目把 `ci_config_path` 指向：

```
.gitlab-ci/entrypoint.yml@engineering/ci-templates:main
```

意思是**项目的 `.gitlab-ci.yml` 不是入口**。实际入口是 `engineering/ci-templates` 仓库的 `entrypoint.yml`，它：

1. 通过 `include: project` 把项目的 `.gitlab-ci.yml` 引进来
2. 强制注入这几个 job（**本表是全仓 SSOT**，其他 skill 引用这里，别各写各的）：

| stage | job | 触发条件 | 失败是否阻断 |
|---|---|---|---|
| `.pre` | `global:credentials-scan` | 仅 `merge_request_event`，timeout 10min | 阻断 |
| `.pre` | `global:repo-boundary-lint` | 仅 `merge_request_event`；黑名单含 `DEV/k8s`、`DEV/crossplane-infra`、`DEV/argocd-apps`、`engineering/ci-templates`、`engineering/skills` | 阻断 |
| `test` | `ci:init` | MR 事件，或 push 到 `main`/`master`/`develop`/`staging` | anchor，恒 pass |
| `.post` | `global:code-review` | **仅 MR 事件且目标分支 protected**；命中后分三档（见下） | 看档 |

**`global:sonarqube-scan` 不在默认入口**。它只出现在 `entrypoint-sonar.yml`（＝ `entrypoint.yml` ＋ `jobs/sonarqube-scan.yml`），
由 `sync_ci_config.py` 依 `.gitlab-ci/config/global.yml` 的 `sonarqube.admission.admitted_projects` 切换。
**未准入的项目永远看不到它**——各仓自己配的 `sonarqube-check` 是业务 job，与它无关。

`global:code-review` 的三档（决定它对某个仓到底是不是门禁）：

| 档 | 哪些仓 | 失败 |
|---|---|---|
| 黑名单 | `KX/proto` | 不跑 |
| **强制门禁** | `^applications/`、`^platforms/`、`^provisioning/`、`^services/`、`^test/`、`engineering/mr-analytics`、`SWCLIEN/g0-android｜g0-flutter-module｜g0-ios` | **非零退出阻断 MR** |
| warn-only | `CLOUD/iot-service-unified`、`DEV/k8s｜crossplane-infra｜argocd-apps`、`engineering/skills`，**以及兜底的其余所有仓** | `allow_failure: true` |

> 实测定版 2026-09-11（buzz 之外的三仓 naturehood 1175 / golf 1296 / launch_monitor 1114 与模板仓源码逐条比对）。
> 改这张表前先重跑一遍「怎么检测」那一节，别凭记忆改。

**契约**：项目的 `stages` **必须包含 `test`**。不包含的话：

- `ci:init` 找不到合法 stage → GitLab 排不进去
- 但这不是 YAML validation error — 配置语法合法，就是没法调度
- 结果：0 jobs、failed 状态、没错误信息

## 怎么检测

看项目的 `ci_config_path`：

```bash
TOKEN=<your token>
curl -s -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('ci_config_path'))"
```

如果输出类似 `...@engineering/ci-templates:...`，这个契约就适用。

也可以直接读模板内容：

```bash
curl -s -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/1055/repository/files/.gitlab-ci%2Fentrypoint.yml/raw?ref=main"
```

（Project 1055 = 写这段时的 `engineering/ci-templates`。）

## 修复

项目 `.gitlab-ci.yml`：

```yaml
stages:
  - test            # 必须 —— ci:init 的 anchor
  # ... 其他项目 stages ...
```

API 主动巡检 自己也放 `stage: test`。跟 `ci:init` 并行没关系。

## 为什么不能自己另起一个 stage？

因为 `ci:init` 你管不着 — 模板注入的，写死在 stage `test`，无法覆盖。如果你用自定义 stage，pipeline 还是会尝试把 `ci:init` 放进 stage `test` 而你没声明 `test` → 同样的失败模式。

永远把 `test` 包进 stages。这是项目间的共同语言。

## 模板注入的其他 job

解决 stage 问题后，每个 MR pipeline 都会看到这些：

| Job | Stage | 行为 |
|-----|-------|------|
| `global:credentials-scan` | `.pre` | TruffleHog 扫 MR commit — 发现已验证的凭证会阻断 |
| `ci:init` | `test` | 纯 echo 的 anchor，总是 pass |
| `global:code-review` | `.post` | AI code review，`allow_failure: true`，通常 pass |
| `global:sonarqube-scan` | `.post` | SonarQube 扫描，`allow_failure: true` |

**别慌**，`code-review` 或 `sonarqube-scan` 在 MR 上标红不代表你的 pipeline 坏了 — `allow_failure` 意味着不阻断。看 `smoke:patrol` 自己是不是绿。

## 模板变更时

项目 CI 没动过但突然 pipeline 挂了，查模板：

```bash
# 模板仓库最近的 commit
curl -s -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/1055/repository/commits?per_page=5" \
  | python3 -c "import sys,json; [print(c['short_id'], c['title']) for c in json.load(sys.stdin)]"
```

模板变更影响所有用 `ci_config_path` 的项目。如果模板改动弄坏了你的项目，要么：
1. 在 `engineering/ci-templates` 开 issue
2. 把项目 pin 到特定模板 ref：`.gitlab-ci/entrypoint.yml@engineering/ci-templates:<sha>`
