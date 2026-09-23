---
name: ingress-security-scan
description: 扫描全部 EKS/TKE/GKE 集群中 ArgoCD 管理的公网 Ingress，找出缺少安全组、inbound-cidrs、WAF 或 ingress.addx.io/sg 标签的公网暴露候选风险。用于定期安全审计或变更后验证。
---

# ingress-security-scan

扫描 16 个 K8s 集群，找出 ArgoCD 管理的、公网暴露且缺少来源限制/WAF 的 Ingress 资源。

**适用场景：**

- 定期安全审计
- 新应用部署后验证 Ingress 是否已配置安全组
- 排查公网暴露风险

## 背景知识

### 安全防护机制

A4x 的 Ingress 安全防护有两种方式：

1. **Kyverno 自动注入**（推荐）：在 Ingress 上添加 label `ingress.addx.io/sg: <value>`，Kyverno 策略根据 value 自动注入对应集群的安全组到 `alb.ingress.kubernetes.io/security-groups`。支持的 value：

   | Label Value | 用途 | 来源 IP | 适用场景 |
   |------------|------|---------|---------|
   | `office` | 办公室白名单 | 公司办公 IP | 内部工具（Grafana/Kibana/管理后台/测试工具）|
   | `internal` | VPC 内网 | 集群 VPC CIDR | 仅供集群内服务调用的 ALB |
   | `feishu-webhook` | 飞书回调 IP | 飞书服务器出口 IP 段 | 接收飞书审批/机器人回调的 webhook Ingress |

   **覆盖情况**以 `~/Project/A4x/k8s/clusters/<cluster>/cicd/kyverno/post-install/policy-inject-*.yaml` 和当前集群 Kyverno policy 为准，不在 skill 内维护静态数字。

2. **手动指定**：直接在 annotation 中设置 `alb.ingress.kubernetes.io/security-groups` 或 `alb.ingress.kubernetes.io/inbound-cidrs`（违反 ING-06，除非有特殊 SG 需求否则不用）

### ArgoCD 资源追踪

ArgoCD 通过以下方式标记 managed 资源（按优先级）：

1. `argocd.argoproj.io/tracking-id` annotation（annotation tracking，最常见）
2. `app.kubernetes.io/instance` label（label tracking）
3. `argocd.argoproj.io/managed-by` annotation

### 判定标准

一个 Ingress 被视为"公网无入口层保护候选"需同时满足：

1. 被 ArgoCD 管理（有上述任一标记）
2. `alb.ingress.kubernetes.io/scheme` = `internet-facing`
3. 无 `alb.ingress.kubernetes.io/security-groups` annotation
4. 无 `alb.ingress.kubernetes.io/inbound-cidrs` annotation
5. 无 `alb.ingress.kubernetes.io/wafv2-acl-arn` annotation

应用层认证无法靠 Ingress YAML 稳定判断。扫描结果应标记为“公网无入口层保护候选”，再人工确认是否有 OAuth/SSO/Basic Auth 等应用层保护。

## 集群列表

| 区域 | 集群 | kubectl context |
|------|------|----------------|
| US | us-tech-service | `arn:aws:eks:us-east-1:002497567426:cluster/us-eks` |
| US | us-prod | `arn:aws:eks:us-east-1:302571458622:cluster/us-eks` |
| US | us-data | `arn:aws:eks:us-east-1:769494896000:cluster/us-prod-data` |
| US | us-staging | `arn:aws:eks:us-east-1:390709477306:cluster/us-eks-staging` |
| EU | eu-tech-service | `arn:aws:eks:eu-central-1:010840394398:cluster/eu-eks-tech-service` |
| EU | eu-prod | `arn:aws:eks:eu-central-1:740315635167:cluster/eu-eks` |
| EU | eu-data | `arn:aws:eks:eu-central-1:769494896000:cluster/eu-prod-data` |
| EU | eu-staging | `arn:aws:eks:eu-central-1:390709477306:cluster/eu-eks-staging` |
| CN | cn-tech-service | `arn:aws-cn:eks:cn-north-1:589899215075:cluster/cn-eks-tech-service` |
| CN | cn-prod | `arn:aws-cn:eks:cn-north-1:741924744516:cluster/cn-eks` |
| CN | cn-dev | `arn:aws-cn:eks:cn-north-1:801447536674:cluster/cn-eks-dev` |
| CN | cn-staging | `arn:aws-cn:eks:cn-north-1:801447536674:cluster/cn-eks-staging` |
| SG | sg-devops | `arn:aws:eks:ap-southeast-1:125710977284:cluster/sg-eks` |
| TKE | tke-cn-main | `tke-cn-k8s` |
| GKE | gke-tech-service | `gke_a4xcloud-tech-service-us_us-east4_us-tech-service-east4-gke` |
| GKE | gke-prod | `gke_a4xcloud-p-us_us-east4_us-prod-east4-gke` |

## 执行流程

### Step 1: 并行扫描全部集群

先用 `kubectl config get-contexts -o name` 校验 16 个 context。标准 context 不存在但本地有明确的等价别名时，记录映射后使用别名；不得因 context 缺失静默跳过集群。每个后台进程都要输出 `scan_ok` 或 `scan_error`，只有 16 个集群全部 `scan_ok` 时才能报告“全量扫描完成”；不可达集群必须标记 `UNKNOWN`，不能记为 `PASS`。

对每个集群执行 `kubectl get ingress --all-namespaces -o json`，用 jq 过滤：

```bash
kubectl get ingress --all-namespaces --context "$CTX" -o json | \
  jq -r '
    .items[] |
    # 1. ArgoCD managed only
    select(
      .metadata.annotations["argocd.argoproj.io/tracking-id"] != null or
      .metadata.labels["app.kubernetes.io/instance"] != null or
      .metadata.annotations["argocd.argoproj.io/managed-by"] != null
    ) |
    {
      ns: .metadata.namespace,
      name: .metadata.name,
      argoApp: (
        if ((.metadata.annotations["argocd.argoproj.io/tracking-id"] // "") | length) > 0 then
          (.metadata.annotations["argocd.argoproj.io/tracking-id"] | split(":")[0])
        elif ((.metadata.labels["app.kubernetes.io/instance"] // "") | length) > 0 then
          .metadata.labels["app.kubernetes.io/instance"]
        elif ((.metadata.annotations["argocd.argoproj.io/managed-by"] // "") | length) > 0 then
          .metadata.annotations["argocd.argoproj.io/managed-by"]
        else
          "unknown"
        end
      ),
      scheme: .metadata.annotations["alb.ingress.kubernetes.io/scheme"],
      sg: .metadata.annotations["alb.ingress.kubernetes.io/security-groups"],
      cidrs: .metadata.annotations["alb.ingress.kubernetes.io/inbound-cidrs"],
      waf: .metadata.annotations["alb.ingress.kubernetes.io/wafv2-acl-arn"],
      sgLabel: .metadata.labels["ingress.addx.io/sg"],
      hosts: [.spec.rules[]?.host // "N/A"]
    } |
    # 2. internet-facing + no security restrictions
    select(
      .scheme == "internet-facing" and
      .sg == null and
      .cidrs == null and
      .waf == null
    ) |
    "\(.ns)/\(.name) | app=\(.argoApp) | \(.hosts|join(","))"
  '
```

为提高效率，应使用 bash 脚本并行扫描所有集群（每个集群一个后台进程），最后汇总结果。

### Step 2: 查询责任人（必做，不得跳过）

**责任人字段是报告的必选列，不是可选项。** 没有责任人的报告无法分派整改任务，扫描等于白扫。即使扫描结果为 0 裸奔，该 Step 可跳过；只要存在裸奔 Ingress，就必须给每个都标注责任人。

**推荐路径：更新远端引用后查本地仓库 git log（最可靠、无需 token）**

先到 `~/Project/A4x/argocd-apps` 更新 `origin/main` 引用，再从该 ref 查 ArgoCD Application YAML，从 `spec.source.repoURL` 解析业务仓库名，最后到 `~/Project/A4x/<repo>` 查指定 ref 的 git log。**不要依赖任一仓库当前工作树的分支或文件，也不要 checkout/switch**；工作树可能落后或有用户未提交修改。

```bash
# 1. 以最新 origin/main 找到 Application；工作树落后时也不会误判孤儿
cd ~/Project/A4x/argocd-apps
git fetch origin main
ARGOCD_APPS_REF=origin/main
APP_FILE=$(git grep -l -E "^[[:space:]]*name:[[:space:]]+$ARGO_APP_NAME([[:space:]]|$)" "$ARGOCD_APPS_REF" -- '*.yaml' '*.yml' | sed 's#^[^:]*:##' | head -1)
if [ -z "$APP_FILE" ]; then
  echo "最新 $ARGOCD_APPS_REF 中找不到 $ARGO_APP_NAME；按孤儿 Ingress 路径调查。"
  exit 0
fi
APP_YAML=$(git show "$ARGOCD_APPS_REF:$APP_FILE")
REPO_URL=$(printf '%s\n' "$APP_YAML" | awk '$1 == "repoURL:" {print $2; exit}')
APP_PATH=$(printf '%s\n' "$APP_YAML" | awk '$1 == "path:" {print $2; exit}')
TARGET_REV=$(printf '%s\n' "$APP_YAML" | awk '$1 == "targetRevision:" {print $2; exit}')

# 2. 切到本地业务仓库（名称 = repoURL 最后一段去掉 .git）
REPO_NAME=$(basename "$REPO_URL" .git)
REPO_DIR="$HOME/Project/A4x/$REPO_NAME"
if [ ! -d "$REPO_DIR/.git" ]; then
  echo "本地缺少 ~/Project/A4x/$REPO_NAME；责任人填 '-' 并在脚注说明。不要从 ~/.claude/password 读取账号密码，不要克隆到 /tmp。"
  # 在批量脚本中这里应 continue 当前 Ingress；如需克隆，先用已有 Git 凭据克隆到 ~/Project/A4x/
else
  cd "$REPO_DIR"

  # 3. 只更新 ArgoCD 指定 ref，不 checkout（targetRevision 可能是 branch/tag/commit）
  git fetch origin "$TARGET_REV"
  if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
    # 浅历史的“最早提交”不是真实创建者；补全后重新 fetch 目标 ref
    git fetch --unshallow origin
    git fetch origin "$TARGET_REV"
  fi
  SOURCE_REF=FETCH_HEAD

  # 4. 从候选中按 metadata.name/namespace/host 选精确源文件，并跟踪 Kustomize/Helm 生成链
  git grep -l -E '^[[:space:]]*kind:[[:space:]]+Ingress([[:space:]]|$)' "$SOURCE_REF" -- '*.yaml' '*.yml'
  INGRESS_FILE='<确认后的精确源文件路径>'

  # 创建者 = 最早相关提交；最后修改者 = 最新相关提交
  git log "$SOURCE_REF" --reverse --format='%ad %an: %s' --date=short -- "$INGRESS_FILE" | head -1
  git log "$SOURCE_REF" -n 1 --format='%ad %an: %s' --date=short -- "$INGRESS_FILE"
fi
```

**备用路径：GitLab API（当本地仓库不可用时）**

```bash
# password 文件里只有账号密码，没有 PAT token。用基本认证 clone 代替 API
# 或者用户显式提供 GITLAB_TOKEN 环境变量时才走 API
GITLAB_TOKEN="$GITLAB_TOKEN"
PROJECT="GROUP%2FREPO"  # URL encoded，如 CLOUD%2Fpremium-management
FILEPATH="k8s%2Fbase%2Fingress.yaml"
curl -s -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/${PROJECT}/repository/files/${FILEPATH}/blame?ref=${BRANCH}" | \
  jq -r '[.[] | .commit] | {creator: first.author_name, lastModifier: last.author_name}'
```

**并行化：** 多个 Ingress 的责任人查询应并行执行（bash `&` + `wait`），避免串行等待。

**关键注意事项：**
- 只有在成功更新后的 `origin/main` 中仍找不到 App，才能判定孤儿；不得用落后工作树判定
- `targetRevision` 经常是 `staging` / `main` 而非 `master`；使用 `FETCH_HEAD` 或明确远端 ref 查文件，不要 checkout
- 如果业务仓库是 shallow clone，必须补全历史后再确认创建者；否则只能标注“浅历史边界，未确认”
- Ingress 文件通常在 `k8s/base/`（或 `k8s/frontend/base/`、`backend/k8s/base/` 等），不在 overlays
- 一个文件包含多个 Ingress 时，结合 `git blame "$SOURCE_REF" -- "$INGRESS_FILE"` 和资源块历史确认最后修改者，不能直接把文件最新提交归给所有资源
- 如果某个 ArgoCD App 在最新 `origin/main` 中找不到（可能被 rename 或删除），该 Ingress 是**孤儿**，在报告中显式标注 `孤儿 Ingress`，责任人按 namespace 所有者 + 最近接触过该仓库的人推断
- 查询失败时，责任人列填 `-` 并在脚注说明，不得阻塞整份报告产出

**孤儿 Ingress 特征：**
- tracking-id 指向的 ArgoCD App 在更新后的 `argocd-apps/origin/main` 中找不到
- 通常有 `kubectl.kubernetes.io/last-applied-configuration` annotation（手动 apply 过）
- 源仓库可能已 rename 过 App（如 `premium-management-web-prod-us` → `premium-management-frontend-prod-us`）

### Step 3: 分类汇总

将结果按以下维度分类：

1. **CICD 基础设施**：ArgoCD App 名称为 `argocd`、`casdoor`、`harbor` 的资源
2. **业务应用**：其余所有资源，按集群分组

### Step 4: 输出报告

输出 Markdown 格式报告：

```markdown
# Ingress Security Scan Report

**扫描时间**: {date}
**集群数量**: 16 | **公网无入口层保护候选 Ingress 数量**: {count}

## 裸奔 Ingress 列表

### 按责任人分组（推荐）

优先按责任人分组而非按集群，便于直接派单：

| 负责人 | 集群 | ArgoCD App | Namespace/Ingress | 域名 | 类型 | 严重度 |
|--------|------|------------|-------------------|------|------|-------|
| {lastModifier} | {cluster} | {app} | {ns}/{name} | {host} | {内部工具/C端/Webhook/staging} | {P0/P1/P2} |

### 按集群分组（补充视角）

| 集群 | ArgoCD App | Namespace/Ingress | 域名 | 责任人 | 修复建议 |
|------|-----------|-------------------|------|--------|---------|
| {cluster} | {app} | {ns}/{name} | {host} | 创建: {creator} / 最后修改: {lastModifier} | 添加 label `ingress.addx.io/sg: office` |

## 修复方法

在 Ingress YAML 的 metadata.labels 中添加：

\```yaml
labels:
  ingress.addx.io/sg: office
\```

Kyverno 会自动注入对应集群的 from-office 安全组。

## 集群汇总

| 集群 | 裸奔数量 | 状态 |
|------|---------|------|
| {cluster} | {count} | {PASS/FAIL} |
```

### Step 5: 修复建议

根据 Ingress 的**使用场景**选择对应 label value，不要一刀切推荐 `office`：

| 场景 | 推荐 label | 说明 |
|------|-----------|------|
| 内部工具（管理后台、Grafana、测试平台等）| `ingress.addx.io/sg: office` | 仅办公室 IP 可访问 |
| 仅供集群内服务调用的 ALB | `ingress.addx.io/sg: internal` | 限定 VPC 内网来源 |
| 飞书审批/机器人 webhook 回调端点 | `ingress.addx.io/sg: feishu-webhook` | 限定飞书服务器 IP 段 |
| 面向 C 端用户的公开服务（官网、App API）| **不加 label** + 必须挂 WAF | 报告中标注"已确认公开" |
| 其他 webhook 回调（非飞书）| 不加 label + WAF + 在应用层做签名校验 | 报告中标注"已确认公开" |

**通用注意事项**：
- **不要手动添加** `alb.ingress.kubernetes.io/security-groups` annotation（违反 ING-06，除非有跨账号/自定义 SG 需求）
- 判断"内部工具"的启发式：域名含 `builder.`、`-internal.`、`reportportal`、`factory-tool`、`grafana`、`kibana`、`admin`、`console` 等 → 强烈倾向 `office`
- 判断"C 端公开"的启发式：域名是产品主域（如 `api.addx.live`、app 入口）→ 不 restrict 但必须 WAF

## Examples

### Bad — 裸奔 Ingress（无安全组、无 office label）

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: factory-tool
  namespace: staging-us
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/healthcheck-path: /health
    # 没有 security-groups annotation
    # 没有 inbound-cidrs annotation
  # 没有 ingress.addx.io/sg label
spec:
  ingressClassName: alb
  rules:
    - host: factory-tool-staging-us.addx.live
```

### Good — 内部工具，用 office label

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: factory-tool
  namespace: staging-us
  labels:
    ingress.addx.io/sg: office          # Kyverno 自动注入 from-office 安全组
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/healthcheck-path: /health
spec:
  ingressClassName: alb
  rules:
    - host: factory-tool-staging-us.addx.live
```

### Good — 飞书 webhook，用 feishu-webhook label

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: device-cloud-server-feishu-webhook
  namespace: prod-cn
  labels:
    ingress.addx.io/sg: feishu-webhook  # Kyverno 自动注入飞书 IP 白名单 SG
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
spec:
  ingressClassName: alb
  rules:
    - host: device-cloud-server-feishu.builder.addx.live
```

### Good — 有意公开的 API 端点（无需 office 限制）

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: api-prod-us-safemo-com
  namespace: prod-us
  annotations:
    argocd.argoproj.io/tracking-id: "api-prod-us:networking.k8s.io/Ingress:prod-us/api-prod-us-safemo-com"
    alb.ingress.kubernetes.io/scheme: internet-facing
    # 面向 APP/设备的 API 端点，有意公开，在报告中标注为"已确认公开"
spec:
  ingressClassName: alb
  rules:
    - host: api-prod-us.safemo.com
```
