---
name: gitlab-ci-runner-issues
description: GitLab CI job 卡 pending / 报 403 / runner pod 起不来。诊断 runner tag 错配 / 个人 namespace / CI_JOB_TOKEN 非成员 / NodePool selector 死锁等模式。
---

# Playbook：GitLab CI runner 问题

## 症状

CI job 一直 `pending` 或失败：
- "This job is stuck because the project doesn't have any runners online assigned to it"
- "You are not allowed to download code from this project" (403)
- "provided value \"<namespace>\" does not match \".+-(staging|pre|prod|dev)$\""
- "Pod scheduled but Pending - no nodes match selector"
- runner Pod 起不来 / OOM / 直接没出现

6 种模式。

## 诊断顺序

### Step 1. 看 job 状态 + tags

```bash
JOB_ID=<job-id>
glab ci view --branch <branch>
# 看 "stuck because" / "pending tags: ..."
glab ci status --branch <branch>
```

记下：tags 字段、错误原文、project 是否在 group 下、触发者是谁。

### Step 2. 看是否有可用 runner

```bash
# project 的 group runner 列表
glab api /projects/:id/runners

# 看哪些 runner 在线
glab api /projects/:id/runners | jq '.[] | {id, status, tag_list}'
```

### Step 3. 错误对模式

| 现象 | 模式 |
|---|---|
| "no runners online assigned"，project 在个人 namespace | **模式 1：仓库在个人 namespace** |
| "no runners online assigned"，但 group 有 runner | **模式 2：runner tag 错配** |
| "You are not allowed to download code" (403) | **模式 3：CI_JOB_TOKEN 触发者非成员** |
| `provided value "<namespace>" does not match` | **模式 4：namespace overwrite 被 runner allow-list 拒绝** |
| runner Pod 在 sg-devops 起不来；selector 不匹配 | **模式 5：NodePool selector 死锁** |
| runner pod OOMKilled / 拉镜像失败 | **模式 6：runner 自身故障（平台级）** |

## 各模式修法

### 模式 1：仓库在个人 namespace

hard rule #18：需要 CI 的仓库必须在 group 下（`DEV/` / `EM/` / `AUD/` 等）。个人 namespace `<user>/xxx` 默认 `shared_runners_enabled: false`，且不继承 group runner。

**修法**：
```bash
# 转 group：
glab project transfer <namespace>/<project> DEV
# 或：手动 GitLab UI → Settings → General → Advanced → Transfer project
```

转完之后 group runner 自动可用。

如果转 project 影响很大（已上线、有 CI history）：
- 短期：让 admin 在 project 上手动开 `shared_runners_enabled` 并打 group tag——但这是 workaround，不解决"个人 namespace 不归属 group 治理"的问题
- 长期：还是要转 group

### 模式 2：runner tag 错配

job 的 `tags:` 指定的 tag 跟 project 的可用 runner 不匹配。

```yaml
# .gitlab-ci.yml
build:
  tags:
    - us-tech-amd64        # 这个 tag 在 project group 没 runner = 卡 pending
```

`references/data/clusters.yaml` 列了每个集群对应的 runner tags。CI job tag 必须跟目标集群对应——hard rule #25。

**修法**：
- 改 `tags:` 用对的 runner（按目标集群查 clusters.yaml）
- 或：在 project group 注册新 runner（产 Ops Todo "ops 注册 runner tag X 给 group Y"）

### 模式 3：CI_JOB_TOKEN 触发者非成员

GitLab 17+ CI_JOB_TOKEN 继承触发用户的项目权限。**Admin 但非项目成员** 触发 MR → trufflehog / credentials-scan job git clone 自身仓库时报 403。

**判别**：
- sibling 项目同 runner 同 job 同时间正常 + project 内别人提的 MR 全过，**唯独你的挂** = 用户维度问题
- error 原文 "You are not allowed to download code from this project"

**修法**：admin 把自己加成 project Developer（access_level=30）+ retry 同 pipeline：

```bash
glab project add-member <namespace>/<project> <user> --access-level 30
```

成员加完后 fresh pipeline 即可。

### 模式 4：namespace overwrite 被 runner allow-list 拒绝

GitLab Runner 在创建 Kubernetes job pod 前会校验 `KUBERNETES_NAMESPACE_OVERWRITE`。部分旧 runner 仍只允许 namespace 形如 `.+-(staging|pre|prod|dev)`；新 CI namespace 必须使用环境前缀，并以报错中的实际 allow-list 判断平台迁移是否完成。

**修法**：
- 新 CI IRSA job 使用独立的前缀式 CI namespace，例如 `{$env}-{$app}-ci`，不复用 runtime namespace，也不新增后缀式命名。
- runner 若只允许历史后缀式正则，先由平台增加前缀式 allow-list；对仍在使用的存量后缀 namespace 只保留精确兼容项。
- 同步改四处：CI ServiceAccount namespace、RoleBinding namespace、IAM trust policy `system:serviceaccount:<namespace>:<sa>`、`.gitlab-ci.yml` 的 `KUBERNETES_NAMESPACE_OVERWRITE`。
- `kustomize build` 后确认 CI ServiceAccount 没有被 overlay 顶层 `namespace:` 覆盖回 runtime namespace。

### 模式 5：NodePool selector 死锁

runner Pod 调度到 sg-devops 集群（CI/CD 基础设施集群），但 NodePool selector 不匹配。

**判定特征**：
- `base-image-sync` NodePool 上必须有 `node-group=gitlab-runner` label
- 没有 → sg-amd64 runner 的 default selector + KUBERNETES_NODE_SELECTOR_NODEPOOL 追加后交集为空 → Pod Pending

**修法**：
- 看 runner pod 的 events：`kubectl -n gitlab-runner describe pod <pod>` → "no nodes match selector"
- 看 NodePool yaml：`kubectl get nodepool` → 确认目标 NodePool 有期望 label
- 不匹配 = 集群级配置错；产 Ops Todo "sg-devops NodePool <name> 加 node-group=gitlab-runner label"

### 模式 6：runner 自身故障

runner pod 直接没出现 / OOMKilled / 拉镜像失败 / runner controller 自己出问题。

```bash
# 看 runner pod
kubectl -n gitlab-runner get pods
kubectl -n gitlab-runner describe pod <runner-pod>

# 看 runner controller logs
kubectl -n gitlab-runner logs <gitlab-runner-controller-pod>
```

不该业务侧修。STOP 产 Ops Todo "sg-devops gitlab-runner 故障：<具体 pod 名 + 错误>"。

常见根因（平台侧）：
- runner image 拉不到（registry 凭据 / GFW）
- runner controller OOM（资源 limit 太小）
- runner registration token 过期

### 模式 7：Job OOMKilled 或构建超时（资源不足）

CI job 被 OOMKilled（exit code 137）或构建时间异常长。

**判别**：
- Pod events 显示 `OOMKilled` 或 `memory limit exceeded`
- 构建时间比预期长 3-5 倍（CPU throttle）
- `kubectl -n gitlab-runner describe pod <pod>` → `Last State: Terminated, Reason: OOMKilled`

**修法**：升级到更高规格的 Runner。sg-devops 提供 4 档（standard / high / xhigh / ultra），在原有 tag 后加后缀即可：

```yaml
# 从 standard 升到 high
default:
  tags:
    - sonar-scanner-sg-high   # 原来是 sonar-scanner-sg

# 从 standard 直接升到 ultra（Android/大型 Java）
default:
  tags:
    - sg-amd64-ultra          # 原来是 sg-amd64
```

各档资源：standard (1.5c/1.5Gi) → high (3.5c/4Gi) → xhigh (5c/8Gi) → ultra (7.5c/24Gi)。

逐级升档，不要直接跳到 ultra。详见 `gitlab-instance-runners` skill 的"SG Runner 资源分档"章节。

## 为什么不走 <替代方案>

- **改用 shared runner** —— hard rule #25 + #11 不允许；不同集群 Harbor 不一样，用 shared runner 三方不一致必崩
- **打 untagged job**（不写 tags）→ 没 tag 的 job fall back 到 shared runner，同上问题
- **跑 docker:dind 自建 runner** —— hard rule #9 + #12 禁止；Kaniko 是当前标准
- **跳过 trufflehog / credentials-scan job** —— 模式 3 临时绕开可行，但 fleet-wide 合规审计过不了

## 参考

- hard-rules.yaml #8 / #9 / #18 / #25
- references/data/clusters.yaml（每集群对应 runner tags）
- NodePool selector 死锁反例：runner 默认 selector 与 job 覆盖 selector 交集为空会让 Pod 长期 Pending。
- CI_JOB_TOKEN 权限反例：跨项目访问未授权时会在 clone / fetch 阶段直接 403。
