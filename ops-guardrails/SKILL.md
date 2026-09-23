---
name: ops-guardrails
description: 运维 AI 在执行有风险的基础设施动作前的预检 checklist。当 AI 准备做这些事时强制触发：申请公网 DNS 解析、改 Ingress/SG/NodePool、push 到 master、跨仓库 glab、kubectl apply 到非 GitOps 路径、写 ExternalSecret/Rollout/AnalysisTemplate/TKE Ingress、把新 Application 部署到已有 namespace、给用户断言根因或写设计方案。
---

# ops-guardrails

## Description

A4x 运维 AI 的"动作前必检"清单。**不是教学文档**，是 AI 在自己即将执行某类动作时强制走一遍 checklist 的拦截器。规则来自 Codex 全局 `AGENTS.md` 网络安全硬性规则、k8s 项目文档/事故复盘和明确 feedback。

与 `cicd-developer` / `cicd-opensource` / `argocd-deploy` 是**互补**关系——那些是"怎么做"，本 skill 是"做之前先停一下检查"。

## Rules

### 何时必须触发

下面任一**关键词或动作**出现，必须先走对应章节的 checklist，**不要先动手**：

| 触发动作 / 关键词 | 走哪一节 |
|---|---|
| 加 DNS、添加解析、`scheme: internet-facing`、改 Ingress annotations、新建公网 ALB/CLB | §1 网络与公网暴露 |
| `PubliclyAccessible: true`、`0.0.0.0/0` 入站规则、给 RDS/ES/Redis/中间件 UI 开公网 | §1 网络与公网暴露 |
| 跨区域访问 RDS / 第三方 API、新 workload 需要稳定出口 IP、加 SG 白名单、新建 NodePool、动 NAT 路由、**把 Pod/workload 迁到私有子网池（nat-egress/cicd-system/crossplane-providers）或改 DRC/nodeSelector 让它落私有节点** | §2 出站与 NAT |
| `git push` 目标是 master/main、`glab mr <subcmd> <IID>`、创建 MR、长任务要在共享仓库改东西、新建 GitLab 仓库 | §3 Git 与 MR |
| 主动 `kubectl apply`、改 `clusters/*/resources/staging-us/*`、删 apisix YAML、CI 里写 kubectl/helm | §4 GitOps 边界 |
| 新 Application / Helm release 部署到一个 namespace、`prune: true` 接管已有资源 | §5 Namespace 与替换 |
| 写 ExternalSecret、决定 Vault 路径写到哪、多个 ES 指向同一个 Secret | §6 ESO 与 Vault 路径 |
| 写 Rollout / AnalysisTemplate、改 PromQL rate 窗口、canary 加权策略 | §7 Rollouts 与 Analysis |
| 在 cn-k8s (TKE) 集群写或改 Ingress | §8 TKE Ingress |
| 准备给用户讲根因 / 写设计方案 / 说"违反约束"或"Skill 教错了" | §9 推断与决策纪律 |

只读动作（`get`/`describe`/`list`/读 YAML/查 Argo CD UI/查 git log）**不触发**本 skill。

### §1 网络与公网暴露

来源：Codex 全局 `AGENTS.md` §Network Safety Red Lines 和 k8s GitOps 文档。**无例外**。

### 1A. 申请公网 DNS（指向 ALB/CLB/公网 IP）

不得直接添加，先核查解析目标背后的服务：

1. **找 Ingress / LB**：`kubectl get ingress -n <ns> <name> -o yaml`
2. **判"是否裸奔"**——以下三项**都没有**才是裸奔：
   - 来源 IP 限制（`alb.ingress.kubernetes.io/security-groups` 指向白名单 SG，如 `ingress.addx.io/sg=office` 注入的 from-office SG；或 `inbound-cidrs` 非 `0.0.0.0/0`）
   - WAF（`alb.ingress.kubernetes.io/wafv2-acl-arn` 或云厂商等价）
   - 应用层认证（SSO / OAuth / Basic Auth，**不是裸登录页**）
3. **判"是否内部工具"**——admin / Grafana / Prometheus / Kibana / Sourcebot / Sourcegraph / RabbitMQ-UI / Kafka-UI / Redis Commander / 数据库 / 队列控制台 / 调试入口都是
4. **决策**：
   - 内部工具 + 裸奔（三项全无）→ **拒绝添加**，回复申请人改造（加 office IP 白名单 SG，或 `scheme: internal` + 内网域名 `*.internal.addx.live`，或补应用层 SSO）
   - 面向 C 端的公开服务（官网 / App 入口 / OAuth 回调）→ 来源 IP 不限定，**WAF 必须挂**
   - 内部工具 + SG / WAF / SSO 任一项成立 → 可加；已绑定 office 白名单 SG 的 Ingress 不强制要求 WAF

参考扫描工具：skill `ingress-security-scan`。

### 1B. 内部服务暴露公网

**禁止**：

- RDS / ElastiCache / OpenSearch 开 `PubliclyAccessible: true`
- SG 入站规则 `0.0.0.0/0`（除非是面向 C 端服务的 ALB，仍要挂 WAF）
- 把"调试方便"作为开公网理由——用 `kubectl port-forward` 或 VPN

---

### §2 出站与 NAT

来源：Codex 全局 `AGENTS.md` §Network Safety Red Lines + k8s 事故复盘 / plans。

### 2A. 新 workload 需要稳定出口 IP（白名单场景）

1. **不要新建** `<workload>-egress` 专用 NodePool，**也不要**继续用 `sentry-egress` 这种窄化名——通用名 `nat-egress`
2. 先在目标集群目录看现有 NAT 出口池（`clusters/<cluster>/karpenter/nat-egress.yaml`，或 `kubectl get nodepool`）。当前已有 `nat-egress.yaml` 的 EKS 集群很多，**不要靠记忆维护列表**，直接查：
   ```bash
   find ~/Project/A4x/k8s/clusters -path '*/karpenter/nat-egress.yaml' -printf '%P\n' | sort
   ```
   没有专用 NAT 池的新集群，新建时统一命名 `nat-egress`。
3. 已有 `nat-egress` 时直接给 Pod 加 `nodeSelector` + 匹配 toleration；容量不够 bump 现有池 `spec.limits`，不新建
4. 遇到窄化名做联动 MR 改成 `nat-egress`：k8s 仓 + 各消费者仓 + skills/docs，分开 MR、按顺序合并
5. **必须验证**：Pod 内 `curl checkip.amazonaws.com`，与 `aws ec2 describe-nat-gateways` 的 PublicIp 交叉核对

### 2B. NAT 路由配置

- NAT gateway 应在独立专用子网（/28 即可），该子网路由表只有 `0.0.0.0/0 → IGW`
- **绝不能在 NAT 所在子网的路由表上添加指向该 NAT 的路由**——会环路
- 工作负载子网路由表才指向 NAT
- 排查 NAT 不通：先看 NAT 子网路由表是否指向自己。`traceroute` hop 2 到 NAT 私有 IP、hop 3 全超时 = 环路

### 2C. SG 白名单源 IP

- **禁止**把节点的 ephemeral / 动态公网 IP 加白名单——节点重建即失效
- 必须用 NAT EIP 或 office 静态 IP

### 2D. 把工作负载迁到私有子网池前——peering 路由 parity 预检

来源：2026-06-10/11 连环事故 **4 例**（302/740/002 ESO→Vault + cn-dev ESO→builder Vault + cn-dev repo-server→内部 Harbor OCI），全因「把消费者迁到私有子网池后，私有子网路由表缺到依赖所在 VPC 的 peering 路由」。详见 memory `private-subnet-peering-route-gap-vault-eso-incident`。

把任何 Pod 迁到**私有子网池**（`nat-egress` / `cicd-system` / `crossplane-providers` 等走 NAT 的池，或改 DRC/values `nodeSelector` 让它落私有节点）**之前**，核对它的跨 VPC 依赖在池的**每一张**私有子网 RT 里都有 peering 路由：

1. **列依赖**：该负载要连哪些跨 VPC 的内部端点？常见——Vault（`vault-{us,eu,cn}-internal[.builder].addx.live`）、内部 Harbor（`registry-harbor-*`，**OCI base chart 依赖也算**，repo-server 拉它要够得着）、跨账号 RDS、其它 `*-internal.addx.live` / `internal-*.elb` ALB。
2. **解析落点**：`getent hosts <host>` 看私有 IP 落哪个 /16（=哪个 VPC/账号）。10.211=us-data、10.222=eu-tech、10.228=cn-tech（**CN builder Vault 在 cn-tech 不在本集群**）、10.236=cn-prod、10.237=cn-dev……
3. **逐张比对**：枚举池的全部私有子网 → 各自 RT（**同集群多张子网可能是不同 RT，cn-dev 3 张只 1 张有路由→间歇性故障，落哪个子网决定通不通**）→ 确认每张都有 `<依赖CIDR>/16 → pcx-<peering>`。用**公有子网 RT 做对照**（公有常有全套 peering，私有缺哪条一目了然）。
4. **缺则补**：`aws ec2 create-route --route-table-id <rt> --destination-cidr-block <dep>/16 --vpc-peering-connection-id <pcx>`；回程侧确认有宽 /16 回程路由（通常已有）。
5. **验证**：消费者起来后实测——CSS 看 `.status.conditions[?(@.type=="Ready")].status==True`（**不是 `kubectl get clustersecretstore` 的 capabilities 列 `ReadWrite`，那个永远在**），或 debug pod 裸 TCP。

**陷阱**：
- SG drop 和黑洞路由**症状完全相同**（静默超时 / `unable to create client` / `context deadline exceeded`）——**先查 RT peering，别先怪 SG**（Vault/Harbor 内部 ALB 前端 SG 入站常本就 `0.0.0.0/0`）。
- ArgoCD app `Synced·Degraded` 但 `health.message` 空、`status.resources` 子资源全 `health=-` → 查带自定义健康检查的 CRD：`ClusterSecretStore`/`ExternalSecret` 的 `Ready` 条件会 roll up 成 app Degraded 但**不在 resources 内联显示**，直接 `kubectl get clustersecretstore <n> -o jsonpath` 看 conditions。
- builder 域集群（cn-dev / cn-staging / us/eu-staging）的 CSS 名是 `vault-builder-backend`（指向 builder Vault），别只扫 prod 的 `vault-backend`。

---

### §3 Git 与 MR

来源：memory `feedback_no_direct_push` / `feedback_glab_cross_repo_id` / `use-worktree-for-shared-repos` / `engineering-skills-mr-doc-link-check` / `new-gitlab-repo-runner-gotchas`。

### 3A. 直推 master/main：禁止

所有变更走 MR：建分支 → 推送 → 创建 MR → 用户审核合并。紧急修复也不豁免（曾因直推 master 删 ArgoCD Ingress 全集群挂）。

### 3B. glab 跨仓库 MR 操作

`glab mr <subcmd> <IID>` 按 cwd 的 `git remote` 推断 project，**跨仓库会误命中 cwd 同号 MR**：

```bash
# 先确认 cwd 对应的 project
git remote get-url origin

# 跨仓一律走 REST API，显式带 project id
glab api --method PUT "projects/<project_id>/merge_requests/<iid>" --field "description=$DESC"
```

`project_id` 从 `glab api "projects/<url-encoded-path>"` 或 list MR 结果取。

### 3C. 共享仓库（k8s / argocd-apps / vault-policies / skills / crossplane-infra）长任务

主 checkout 会被并发 AI 进程切走分支导致 commit 错乱。**必须**用 git worktree：

```bash
cd ~/Project/A4x/k8s
git fetch origin
git worktree add ~/Project/A4x/k8s.<topic> -b <branch> origin/master
cd ~/Project/A4x/k8s.<topic>     # 所有 edit/commit/push 都在这里
# 完成后
cd ~/Project/A4x/k8s
git worktree remove ~/Project/A4x/k8s.<topic>
git worktree prune
```

路径约定：`<主 repo>.<topic>` 同级目录。

### 3D. 新建 GitLab 仓库

API 创建的仓库默认两个坑：

1. `shared_runners_enabled=false` → CI stuck "no runners that match all tags"
   - 修：`glab api projects/$ID --method PUT -F "shared_runners_enabled=true"`
2. 首个 push 的分支自动 protected → `force-with-lease` 被拒
   - 修：`glab api projects/$ID/protected_branches/<url-encoded-branch> --method DELETE`
   - 更优：建仓后**先 push 一个空 main**，再推 feature branch

### 3E. `default.before_script` 兼容 compliance

group compliance 注入 `global:credentials-scan`（trufflehog 镜像）会继承项目 `default.before_script`。**任何依赖项目镜像 binary 的命令必须 guard**：

```yaml
default:
  before_script:
    - command -v go >/dev/null 2>&1 && go env || true
```

特征信号：branch-push pipeline 成功 + MR pipeline `.pre` `script_failure` + trace 末尾 `<bin>: command not found` = compliance 继承踩坑。**不要**删 `default.before_script`，也**不要**改 group compliance。

---

### §4 GitOps 边界

来源：memory `us-staging-ingress-not-gitops` / `apisix-apply-no-delete` + cicd-developer 的"CI 不部署"红线。

### 4A. CI 禁止部署动作

CI 只构建镜像。**禁止** `kubectl apply` / `helm install` / `helm upgrade`。部署一律 ArgoCD。

### 4B. 非 GitOps 纳管路径（合并 ≠ 生效）

`clusters/aws-002497567426-us-tech-service/resources/staging-us/` 下的 `ingress/` `service/` `deployment/` `configmap/` 等**不在任何 ArgoCD App 监听范围**（资源仅有 `k8slens-edit-resource-version` 标签，无 `argocd.argoproj.io/tracking-id`）。

改动这些目录后：

- MR 描述里**必须**点明"该路径非 GitOps 纳管，合并后需手工下发"
- MR 合并后**手工** `kubectl apply -f ...`
- 不下发就是 Git ↔ 集群 drift

### 4C. apisix-gateway 删除资源

`apisix-gateway/scripts/apply.sh` 已加 prune 步骤（`APPLY_PRUNE=true` 默认开），合并删除 YAML 的 MR 后会自动清理 etcd 中的孤儿资源——**但仅限** `upstreams` / `global_rules` / `routes` 三类。其它资源仍只 PUT 不 DELETE：

- `consumers/`：保守策略不 prune（可能人工创建），删 YAML 后必须手工 admin API DELETE
- `global-plugins/`、ConfigMap 等其它目录同理：不在 prune 列表的都要手动清

手工 DELETE 操作：

```bash
kubectl -n staging-us port-forward svc/apisix-admin 9180:9180
ADMIN_KEY=$(kubectl -n staging-us get secret apisix-secrets -o jsonpath='{.data.ADMIN_KEY}' | base64 -d)
curl -X DELETE -H "X-API-KEY: $ADMIN_KEY" http://127.0.0.1:9180/apisix/admin/consumers/<id>
```

落地特征：apply Job 日志末尾会打印 `=== Pruning orphan resources (APPLY_PRUNE=true) ===` 一段，列出被 prune 的 id；列出来的不用管，没列的资源类型才需要手工删。

### 4D. CronJob 同步类资源（vault-policies / casdoor-sync）

走 sg-devops 上 K8s CronJob，不走 GitLab CI push。改完 YAML 合并后等 `*/3` 周期，或 `kubectl create job --from=cronjob/...` 手工触发，不要走 CI。

---

### §5 Namespace 与替换

来源：memory `feedback-no-overwrite-existing`。

部署新 Application / Helm release 前：

1. `kubectl get all -n <namespace>` 查 namespace 是否已有运行服务
2. **有 → 必须换独立 namespace**（如新 Harbor 用 `registry` 而非 `harbor`）
3. ArgoCD `prune: true` + `selfHeal: true` 会接管 namespace 内不属于它的资源 → 数据可能保留但服务必断
4. "新旧并行"也要物理隔离（不同 namespace），不要靠 label selector

---

### §6 ESO 与 Vault 路径

来源：memory `eso-shared-target-secret-antipattern` + cicd-developer 的"禁止多个 ExternalSecret 写同一个 target Secret"红线 + 路径选择决策树。

### 6A. 多 ES 共享 target Secret = 反模式

```bash
kubectl -n <ns> get externalsecret \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.target.name}{"\n"}{end}'
```

`target.name` 必须唯一。重复 = 事故现场或事故前夜。两种失效路径：

- **double Merge**：两个 ES 都不创建 Secret → `secret not found`
- **Owner + Merge**：Owner ES server-side apply 反复擦掉 Merge ES 字段 → Pod 重启拿到不完整 Secret 直接挂

正确模式：

- **A（推荐）**：单 ES + `dataFrom.extract`，从多个 Vault 路径一次拉所有字段
- **B**：两个独立 target Secret，各自 Owner，envFrom 引两次

### 6B. Vault 路径决策树

按顺序回答，命中即停：

1. **CI/CD 工具链 / 平台组件自身的凭据**（ArgoCD 拉 Git、Casdoor 连 MySQL、Harbor 后端 S3、gitlab-runner token）→ `secret/cicd/<component>/<key>`，业务 app 不读不写
2. **Crossplane / Platform Admin 生成、给 app 用的平台资源凭据**（AWS IAM key、auth OIDC client secret）→ `secret/{env}/<platform>/application/<app>/<key>`（`<platform>` ∈ `aws` / `auth`）
3. **app 自己拥有自己用的密钥**（业务 DB、第三方 API、Sentry DSN）→ `secret/{env}/app/<app>/<key>`
4. **老应用未在 gitlab-vault-sync 注册** → 维持 `secret/<app>/<env>/*`，不迁移、不双写

**禁止**：

- 业务 app 写 `secret/cicd/*`（policy 直接 403）
- 用 `secret/{env}/app/shared/*` 当共享区（违反 app 路径 == app 所有权）
- 跨 pre/prod 用 Maintainer+ token 给 Developer 代跑（policy 灰区，要 prod 就申请权限）

跨 app 共享第三方凭证 → 当平台组件放 `secret/cicd/<service>/<credential>`，找运维开针对子路径的只读 policy。

---

### §7 Rollouts 与 Analysis

来源：memory `analysis-template-rate-window-and-failcond` / `argo-rollouts-background-analysis-canary-hash` / `canary-alb-argo-rollouts-full-pattern`。

### 7A. AnalysisTemplate 必查项

1. **rate 窗口 ≥ 4 × Prometheus scrape_interval**
   - kubernetes-pods job 是 30s → 窗口 ≥ 2m
   - 查：`kubectl -n prometheus get cm prometheus-server -o yaml | grep scrape_interval`
   - 小于 → `rate()` 样本不足返回空 → empty-guard 自动 pass → 判定失效
2. **必须三段式判定**，只有 `successCondition` 等于没判定：
   ```yaml
   successCondition: len(result) == 0 || isNaN(result[0]) || result[0] >= 0.99
   failureCondition: len(result) > 0 && !isNaN(result[0]) && result[0] < 0.95
   failureLimit: 2
   ```

### 7B. background analysis 必须显式 canary-hash

`canary.analysis`（贯穿 rollout 的后台判定，非 per-step）**不自动注入** `canary-hash`：

```yaml
canary:
  analysis:
    templates:
      - templateName: <name>
    args:
      - name: canary-hash
        valueFrom:
          podTemplateHashValue: Latest   # canary RS hash；Stable 是 baseline
```

漏了 → `InvalidSpec: args.canary-hash was not resolved`，Rollout Degraded（应用本身不受影响，但 ArgoCD 飘红）。

### 7C. ALB 真加权 canary 三件套

`trafficRouting.alb` + background AnalysisTemplate + `ignoreDifferences` 三处必须**同步做齐**，缺一组就走不通。

### 7D. Tier 1 vs Tier 2 准入

- 默认 Tier 1（空 canary strategy，等价 RollingUpdate）
- Tier 2（ALB weighted + Prometheus AnalysisTemplate）准入门槛：staging 流量足够 + `replicas > 2` + 非内部工具
- 不满足门槛强上 Tier 2 → AnalysisRun 永远空结果 → 判定失效或 rollout 卡死

---

### §8 TKE Ingress

来源：memory `tke-qcloud-ingress-tls-required`。

cn-k8s（TKE）集群的 qcloud Ingress：

1. **必须有 `spec.tls`**——`l7-lb-controller` 强制要 HTTPS 监听器证书
2. **复用预置通配符 secret**：`addx-live-2023-wgyxwfot`（staging-cn / prod-cn 命名空间已预置 `*.addx.live`）
   ```yaml
   spec:
     tls:
       - hosts: [<ingress-host>]
         secretName: addx-live-2023-wgyxwfot
   ```
3. 缺 tls → `E4040 CertError` → CLB Listener / Rule / BackendGroup 都不创建 → readiness gate 永卡 False → Rollout `ProgressDeadlineExceeded`
4. **Pin CLB ID**：annotation `kubernetes.io/ingress.qcloud-loadbalance-id` 固定 LB，防止 sync 时 IP 变化
5. 其它 TKE namespace 没预置 secret → 走 ingress-tls 自服务申请或 crossplane-infra 声明

---

### §9 推断与决策纪律

来源：memory `feedback-verify-before-asserting` / `feedback-check-prior-decisions` / `feedback-discovery-before-design` / `feedback-no-premature-wrap-up`。

### 9A. 验证再断言

要给用户讲根因 / 说"X 行为导致 Y" / 说"Skill 教错了" / 说"违反平台约束" 之前：

1. **先 `git log --all` 看完整历史**，特别是 `--diff-filter=A` 找首次创建版本，`git show <commit>:path` 读原始内容
2. 不要从一次观察推断广义行为——"我看到 Secret 只剩 3 个 key" ≠ "Owner ES 在抹字段"
3. 断言"X 导致 Y"要么有官方文档/源码证据，要么自己起 minimal repro 验证
4. 断言"Skill 缺陷"前**完整 grep 该 Skill**，红线和反例往往写在不显眼章节但确实存在
5. 给"修复方案"前先 `git fetch --all` 看 owner 是不是已经在修了
6. 写完一长串分析后才发现错 → **主动撤回**，不要嘴硬

### 9B. 既往决策先于约束

做"设计文档 vs 实现"对比扫描时，**任何"违反强约束"的项目，必须先反向搜既往拍板例外**：

1. 见到"必须 / 禁止 / 强约束"用词 → 立即搜 memory（特别是带 **Why** 推理的 feedback / project 文件）+ 当前会话历史
2. 三档分类：
   - ✅ 一致
   - ✅ 不一致但**已有拍板决策**（带 memory 引用）
   - ⚠️ 不一致**且无既往决策** ← 这才是真 deviation
3. 明确的拍板决策必须**当场**写进 memory，不能等审计时才追认

### 9C. 设计前先摸底

新改造项默认顺序：**摸底 → 方案（多选项对比）→ 评审 → 落地计划 → 代码**。

- 摸底产出：现状事实、约束、痛点、已有相关决策、stakeholder
- 方案产出：多选项对比 + 推荐 + 风险 + 前置条件，**不含**具体代码
- 除非用户说"开始写"，否则不要主动问"要不要我现在写 XX"
- 例外：已有方案的小改动 / bug 修复可省略摸底

### 9D. 不提前收工

TodoWrite 里只要有 `pending`，当前会话就没完。**不要**主动说"收工了"/"今天到这里"/"要不要整理日报"/"下次继续"。继续推下一个 pending，直到清零或用户明确叫停。

阻塞场景说"阻塞在 X 等待 Y"是可以的，但仍继续推能推的 item。

### 用法约定

- 这个 skill 是 **AI 自检拦截器**，不是给用户看的教程。每次触发动作前 AI 自己走完对应章节的 checklist 再执行
- 检查项命中 ⚠️ 或拒绝条件 → 停手 + 把违规项和建议改造方案告诉用户，不要硬上
- 章节随事故 / feedback 持续增补；新增条目必须有 memory 来源或事故引用，不写理论规则

## Examples

### Bad

```
用户：给 sourcebot.addx.live 加个 DNS 解析指到 ALB

AI：
1. 直接调 aliyun DNS API 添加 CNAME
2. 不查 Ingress 是否有 SG / WAF / 应用层认证
3. 等用户事后发现裸奔再补救
```

问题：违反 §1A——内部代码搜索工具是典型的内部工具，加 DNS 前必须先核查三件套，裸奔就拒绝并要求改造，而不是先加再说。

```
用户：在 cn-tech-service 部署一个新服务，要从节点出去访问 cn-prod RDS

AI：
1. 直接新建 sourcebot-egress NodePool
2. 把节点 ephemeral 公网 IP 加到 RDS 安全组白名单
3. 跑通了就交付
```

问题：违反 §2A——应该先 `kubectl get nodepool` 复用现有 `nat-egress`；违反 §2C——节点 IP 是动态的，重建即失效，必须用 NAT EIP。

### Good

```
用户：给 sourcebot.addx.live 加个 DNS 解析指到 ALB

AI：
1. 触发 §1A，先 kubectl get ingress -n sourcebot sourcebot -o yaml
2. 核查三件套：SG / WAF annotation / 应用层认证 → 缺 SG + WAF
3. 判定为内部工具 + 裸奔 → 拒绝直接加 DNS
4. 回复用户："sourcebot 是代码搜索（内部工具），现在裸奔。建议任选其一：
   (a) 加 alb.ingress.kubernetes.io/security-groups 指向 office IP 白名单 SG
   (b) 改 scheme: internal + 走 sourcebot.internal.addx.live 内网域名
   改完再来加 DNS"
5. 不动手，等用户决策
```

```
用户：长任务，在 ~/Project/A4x/k8s 改一批 cluster overlay

AI：
1. 触发 §3C，因为有并发 AI 风险
2. cd ~/Project/A4x/k8s && git fetch origin
3. git worktree add ~/Project/A4x/k8s.cluster-overlay -b feat/xxx origin/master
4. 全程在 worktree 内编辑 / commit / push
5. 完成后 git worktree remove + prune
```

```
用户：跨仓改一个 MR 描述，cwd 在 k8s 仓，要改的是 engineering/skills!333

AI：
1. 触发 §3B，glab mr 跨仓有踩坑风险
2. git remote get-url origin → 确认是 DEV/k8s（不是目标仓）
3. 改走 REST API：
   PROJECT_ID=$(glab api "projects/engineering%2Fskills" | jq -r .id)
   glab api --method PUT "projects/$PROJECT_ID/merge_requests/333" --field "description=..."
4. 不在 k8s cwd 跑 glab mr update 333
```
