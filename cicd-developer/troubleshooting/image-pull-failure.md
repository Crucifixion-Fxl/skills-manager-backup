---
name: image-pull-failure
description: Pod 报 ImagePullBackOff / ErrImagePull。多种根因——镜像不存在 / Harbor 凭据错 / 跨 region pull / multi-arch 不匹配 / 三方一致性破坏 / Rollout stable RS 卡在 bootstrap 镜像。playbook 先判模式再修。
---

# Playbook：ImagePullBackOff / ErrImagePull

## 症状

```
kubectl get pods -n <ns>
```

Pod 状态 `ImagePullBackOff` 或 `ErrImagePull`。`kubectl describe pod` 看 events：

- `Failed to pull image "...": rpc error: code = NotFound`
- `Failed to pull image "...": 401 Unauthorized`
- `Failed to pull image "...": no matching manifest for linux/<arch>`
- `Back-off pulling image`

6 种不同根因。

## 诊断顺序

### Step 1. 抓完整 image reference + 错误码

```
POD=<pod-name>
NS=<namespace>
kubectl -n $NS describe pod $POD | grep -A5 "Failed to pull"
kubectl -n $NS get pod $POD -o jsonpath='{.spec.containers[0].image}'
echo "---"
kubectl -n $NS get pod $POD -o jsonpath='{.spec.imagePullSecrets}'
echo "---"
kubectl -n $NS get pod $POD -o jsonpath='{.spec.nodeName}'
```

记下：image full path、错误码、imagePullSecrets 是否含 `harbor-registry-secret`、pod 在哪个 node。

### Step 2. 错误码对模式

| 错误码 / 现象 | 模式 |
|---|---|
| `NotFound` / `manifest unknown` | **模式 1：镜像 tag 不存在** |
| `401 Unauthorized` | **模式 2：imagePullSecrets 缺失或失效** |
| `no matching manifest for linux/arm64` 或 `linux/amd64` | **模式 3：multi-arch 不匹配** |
| Pod 在某些 node 拉成功，某些失败 | **模式 4：CN 集群跨 region pull / GFW** |
| ImagePullBackOff 持续；CI 推到了 Harbor 但 Pod 拉的是另一个 Harbor | **模式 5：三方一致性破坏（hard rule #25）** |
| Application live image 已是 SHA，但 Rollout stable RS / Pod 仍拉 bootstrap tag（如 `:0000000`）或不存在 tag | **模式 6：Argo Rollouts stable RS 卡在 bootstrap 镜像** |

## 各模式修法

### 模式 1：镜像 tag 不存在

CI 没推 / 推到别的路径 / tag 写错。

```bash
# 看 Harbor 有没有
HARBOR=<目标 Harbor URL>
TAG=<tag>
curl -s -u <user>:<pass> "https://$HARBOR/api/v2.0/projects/cicd/repositories/<app>/artifacts" | jq .

# 看 CI 最近 push 的 tag
glab ci status --branch <branch>
```

**修法**：
- CI 没跑：触发 pipeline
- CI 推到别路径：按 hard rule #11，路径必须 `cicd/{staging|pre|prod}-<region>/<app>`；不是这个路径 = CI 模板有问题，改 `.gitlab-ci.yml` 的 `KANIKO_DESTINATION`
- tag 写错：看 Application `spec.source.kustomize.images`、`argocd app get <app>`
  和 workload image 是否与 CI 推送 tag 一致

### 模式 2：imagePullSecrets 缺失或失效

`kubectl get pod -o yaml` 看 `imagePullSecrets`。

**情况 A：缺失**（pod spec 没 imagePullSecrets）—— hard rule #24 违反：

base/rollout.yaml 必须 **显式** `imagePullSecrets: [{ name: harbor-registry-secret }]`。Argo Rollouts 创建的 Pod 走 default SA + 空 imagePullSecrets，从私有 Harbor 拉 → 走匿名 → 401。

```bash
# 修 base/rollout.yaml 加上：
spec:
  template:
    spec:
      imagePullSecrets:
        - name: harbor-registry-secret
```

push + ArgoCD sync 后下次 rollout 才会有 imagePullSecrets。

**情况 B：有但失效**（secret 内容过期）：

```bash
kubectl -n <ns> get secret harbor-registry-secret -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d
```

看 auth 是否过期。Kyverno 自动同步该 secret 到所有 ns；如果同步坏了 → 平台问题，产 Ops Todo "Kyverno sync harbor-registry-secret 失效"。

### 模式 3：multi-arch 不匹配

CI 只推了 amd64，Pod 调度到 arm64 节点（或反之）。

```bash
# 看 image manifest 含哪些 arch
crane manifest <full-image-path> | jq '.manifests[].platform'
# 期望：含 {architecture: amd64} 和 {architecture: arm64}（dual-arch 应用）

# 看 Pod 在哪个 arch node
kubectl -n <ns> get pod <pod> -o jsonpath='{.spec.nodeName}'
kubectl get node <node> -o jsonpath='{.metadata.labels.kubernetes\.io/arch}'
```

**修法**：
- 新应用：按 `recipes/ci/README.md` 启 dual-arch CI（三件套 build:amd64 + build:arm64 + manifest）
- 老应用钉 amd64：要么修 CI 加 arm64，要么 Rollout 加 `nodeSelector: kubernetes.io/arch: amd64`（违反 hard rule #27，仅作过渡）
- TKE 集群单 arch：不该出现这模式（节点全是 amd64），重新看是不是模式 1 / 5

### 模式 4：CN 集群跨 region pull / GFW

Pod 镜像引用了 `docker.io` / `gcr.io` / `quay.io` 等公网 registry，CN 节点拉不到。

```bash
kubectl -n <ns> get pod <pod> -o jsonpath='{.spec.containers[0].image}'
# 看是不是 docker.io/... 等
```

**修法**：hard rule #10 + #28：

- 写 `${HARBOR_REGISTRY}/base/<image>:<tag>`，**不要** 直接 `FROM docker.io/...`
- base/ 没有该镜像 → 提 DEV/base-images images.yaml MR 加一行
- 合并前先用本地 / US/EU 集群跑，**不要** 在 CN 干等

### 模式 5：三方一致性破坏（hard rule #25）

CI 推 Harbor A，Pod 在集群 X 拉 Harbor B，三方（runner / manifest / Application）对不齐。

```bash
# 三件套都查一遍
# 1. runner 注入的 HARBOR_REGISTRY
glab job log <job-id> | grep HARBOR_REGISTRY

# 2. manifest kustomization.yaml 的 newName
yq '.images[].newName' k8s/overlays/<env-keyword>/kustomization.yaml

# 3. ArgoCD Application 的 image-updater annotation
kubectl -n argo-cd get app <app> -o jsonpath='{.metadata.annotations.argocd-image-updater\.argoproj\.io/image-list}'
```

**三个值必须指向同一 Harbor**。

已验证失败模式：TKE / EKS / 多账号集群的 runner tag 选错时，CI 推送的 Harbor 与目标集群拉取的 Harbor 会不一致，最终表现为 ImagePullBackOff；后台任务可能没有业务告警，必须靠部署验收主动发现。

**修法**：先确认部署侧 overlay newName + Application image-list 是同一字面 host；再按目标集群挑 runner tag（查 `references/data/clusters.yaml -> clusters[].runner_tags`，tag → Harbor 是 1:1）。

- **B 写法**（CI 用 `${IMAGE_BASE}`，host 由 runner 注入，见 hard-rules #25）：症状是绿 pipeline 把镜像 push 到错 Harbor，部署期才 ImagePullBackOff（晚爆）。修 `.gitlab-ci.yml` 的 **tags** 字段（不是去改一个字面 host）让它落到部署侧 host；真·多 target 应加 post-build 部署校验（rollout status / argocd app wait）把它提前暴露在流水线内。
- **A 写法**（CI 字面 host）：错 runner 在 build 期就 401 loud-fail；修法是把 tags + 字面 `IMAGE_PATH` 一起对齐部署侧 host。

部署侧字面 host 与 CI runner tag 解析出的 Harbor 一致后，push 一次。

### 模式 6：Argo Rollouts stable RS 卡在 bootstrap 镜像

这是模式 1 的 Rollout 放大版：Application live override / Image Updater 已写入真实 SHA，但
Rollouts 仍把某个拉不到镜像的旧 ReplicaSet 当作 `stableRS`。这时只改 recovery seed
只能防复发，不能保证已经卡住的 stable 指针自动恢复。

先只读确认三件事：

```bash
APP=<argocd-application>
ROLLOUT=<rollout-name>
NS=<app-namespace>

# 1. Application summary 和 live workload image 是否已经是发布 SHA
kubectl -n argo-cd get app $APP -o jsonpath='{.status.summary.images}{"\n"}'
kubectl -n $NS get rollout $ROLLOUT -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

# 2. Rollout 当前 stable / canary / step 状态
kubectl argo rollouts get rollout $ROLLOUT -n $NS

# 3. stableRS / currentPodHash 与各 ReplicaSet 镜像是否一致
kubectl -n $NS get rollout $ROLLOUT -o jsonpath='{.status.stableRS}{" "}{.status.currentPodHash}{"\n"}'
kubectl -n $NS get rs -l app=$ROLLOUT \
  -o custom-columns=NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas,IMAGE:.spec.template.spec.containers[0].image
```

命中信号：

- Application live override 和 live Rollout image 已经是 Git SHA。
- `kubectl argo rollouts get rollout` 显示 stable ReplicaSet 仍在拉 bootstrap tag（例如 `:0000000`）或一个 Harbor 中不存在的 tag。
- bad stable RS `READY=0`，对应 Pod 是 `ImagePullBackOff` / `ErrImagePull`。
- 新 ReplicaSet 已存在但被 Rollouts 状态挡住，或者 canary/step 不能自然推进。

**修法**：先恢复 live Rollout，再补 Application recovery seed 防复发。

优先尝试 Rollouts 官方恢复命令：

```bash
kubectl argo rollouts retry rollout $ROLLOUT -n $NS
kubectl argo rollouts promote $ROLLOUT --full -n $NS
kubectl argo rollouts undo $ROLLOUT --to-revision=<last-known-good-revision> -n $NS
```

如果 bad stable RS 明确 `READY=0`、没有承接流量、并且上面的命令不能推动状态，才受控缩掉这个坏 RS：

```bash
BAD_RS=<stable-rs-name>
kubectl -n $NS scale rs $BAD_RS --replicas=0
```

缩 RS 前要确认它没有 ready pod、没有承接 Service 流量；不要对健康 stable RS 做这个操作。恢复后持续观察：

```bash
kubectl argo rollouts get rollout $ROLLOUT -n $NS
kubectl -n $NS get pods -l app=$ROLLOUT
kubectl -n argo-cd get app $APP
```

收尾动作：

- 如果期间加过临时强制 image、临时简化 canary steps、或绕过 Image Updater 的 patch，恢复后必须从 Git 移除。
- overlay 中的 `:0000000` 可以保留为 Kustomize placeholder；首次合并 / 同步 Application
  时 `spec.source.kustomize.images` recovery seed 必须是 CI 已推送的真实 SHA。后续 SHA 由
  Image Updater argocd write-back 更新 live Application override，root-app ignore 保护该字段。

## 为什么不走 <替代方案>

- **手动 `kubectl create secret docker-registry`** —— 违反 hard rule #12（禁止自建 imagePullSecret）。Kyverno 自动同步是当前标准；自建会跟同步打架。
- **改 imagePullPolicy: Always** —— 没解决根因，只是反复尝试拉同样不存在的镜像；CI/Harbor/网络问题没动。
- **改 image policy 跳过校验** —— Kyverno disallow-latest-tag (Enforce) 会拦下；不是合理修法。
- **只改 Git baseline tag** —— 对还没部署的新应用有预防价值；对已经卡在 bad stable RS 的 Rollout 不一定能恢复 live 状态。
- **永久保留临时强制 image / 简化 canary patch** —— 会绕开 Image Updater 或真实发布策略，后续容易留下 OutOfSync / 发版不可重复的问题。

## 参考

- hard-rules.yaml #10 / #11 / #12 / #24 / #25 / #27 / #28
- recipes/k8s/rollout.yaml.tmpl（imagePullSecrets 默认配置）
- recipes/ci/README.md（dual-arch 渲染规则）
- 跨环境镜像污染反例：多分支 / 多环境共用 Harbor 路径会让 Image Updater 选中错误镜像。
- 目标集群 Harbor 不一致反例：runner tag、kustomize image、Application annotation 三方任何一处偏离都会拉不到镜像。
