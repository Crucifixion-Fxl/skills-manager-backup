---
name: image-updater-skips-live-image
description: Argo CD Image Updater 没有发布 CI 已推送的新 SHA；Application 仍 Synced/Healthy，日志只有 images_skipped，status.summary.images 为空或缺目标镜像。
---

# Playbook：Image Updater 跳过新 SHA

## 症状

- CI 已向 Application 声明的 Harbor 路径推送新的纯 Git SHA tag。
- Application 与工作负载仍是旧 SHA，但 Application 显示 `Synced/Healthy`。
- Image Updater 没有凭据或 registry error；周期汇总只有 `images_skipped` 增加。
- `argocd-image-updater test` 能选出新 SHA，但常驻 controller 不写回。
- Application 的 `.status.summary.images` 为空，或不包含 `image-list` 指向的镜像。

这不是 Pod 拉取失败。Pod 已经健康运行但镜像没有被更新时才进入本 playbook；
`ImagePullBackOff` / `ErrImagePull` 走 `image-pull-failure.md`。

## 诊断顺序

### Step 1. 核对声明、Application recovery seed 与实际工作负载

```bash
CTX=<context>
APP=<application>
NS=<application-namespace>
WORKLOAD=<rollout-or-deployment-name>

kubectl --context "$CTX" -n argo-cd get application "$APP" -o json | jq '{
  annotations: (.metadata.annotations | with_entries(
    select(.key | startswith("argocd-image-updater.argoproj.io/"))
  )),
  sourceRepo: .spec.source.repoURL,
  sourcePath: .spec.source.path,
  summaryImages: (.status.summary.images // []),
  sync: .status.sync.status,
  health: .status.health.status
}'

kubectl --context "$CTX" -n "$NS" get rollout "$WORKLOAD" -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}' 2>/dev/null \
  || kubectl --context "$CTX" -n "$NS" get deployment "$WORKLOAD" -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
```

确认以下契约仍然存在：

- `image-list` 只指向环境隔离的 Harbor repository，不固定 tag。
- `<alias>.update-strategy: newest-build`。
- `<alias>.allow-tags: regexp:^[a-f0-9]{7,40}$`。
- `write-back-method: argocd`，且没有 `git-branch`。
- Kustomize 应用有 `<alias>.kustomize.image-name`，Application
  `spec.source.kustomize.images` 有与 image-list 同路径的 current recovery seed。

alias 必须从 `image-list` 等号左侧读取，不要假设一定叫 `app`。

### Step 2. 证明 controller 是“跳过”，不是查 registry 失败

```bash
kubectl --context "$CTX" -n argo-cd logs deploy/argocd-image-updater --since=30m \
  | rg "$APP|Could not get tags|unauthorized|images_skipped|Setting new image"
```

命中本模式的信号：

- 周期正常结束且 `errors=0`。
- 没有该 Application 的 `Setting new image`。
- `.status.summary.images` 不包含目标 repository。

如日志有 `unauthorized`、`Could not get tags`、TLS 或 timeout，先修 registry
credential / 网络；不要加 `force-update` 掩盖连接错误。

### Step 3. 用独立 test 隔离 registry 选择逻辑

先查看当前版本和 test 参数，避免把其它版本 CLI 语法硬套进来：

```bash
kubectl --context "$CTX" -n argo-cd get deploy argocd-image-updater \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl --context "$CTX" -n argo-cd exec deploy/argocd-image-updater -- \
  argocd-image-updater test --help
```

annotation-based v0.x 的常见只读测试命令：

```bash
IMAGE=<literal-harbor-repository>

kubectl --context "$CTX" -n argo-cd exec deploy/argocd-image-updater -- \
  argocd-image-updater test "$IMAGE" \
  --registries-conf-path /app/config/registries.conf \
  --update-strategy newest-build \
  --allow-tags 'regexp:^[a-f0-9]{7,40}$' \
  --platforms linux/amd64 \
  --loglevel debug
```

| 结果 | 结论 |
|---|---|
| test 选出新 SHA；summary 缺目标镜像 | 命中 live-image discovery gate，进入修复 |
| test 也选不出新 SHA | 查 allow-tags、平台、Harbor 路径和镜像 created metadata |
| summary 已含目标镜像；test 选出新 SHA | 查 alias/Helm/Kustomize 参数映射和 controller 日志，不先加 force-update |
| test unauthorized / timeout | registry 凭据或网络问题，不属于本模式 |

## 根因

Image Updater 默认只处理 Argo CD Application status 中导出的当前运行镜像。某些
custom resource、PodTemplate 或特定 Application tree 状态不会把目标镜像写进
`.status.summary.images`，controller 因而在访问 registry 之前直接跳过该 alias。

`force-update` 是 Image Updater 为这种场景提供的显式开关：即使 status 没报告该镜像，
仍按 `image-list` 与 alias 参数映射执行更新。它不改变 `newest-build` 或 SHA allow-list。

## 修复门禁

`force-update` 会绕过 current-image discovery，因此它不是 fleet 通用修复。只允许有上述
只读证据的准确 alias，并必须验证 no-change reconcile 不重复写 live Application 或报告
虚假的 `images_updated=1`。

如果同集群普通 Deployment Application 也普遍没有 `status.summary.images`，STOP：
先修 Argo CD Application resource tree/Pod image summary，不能把所有应用批量改成
`force-update`。

对已批准 canary，在 `argocd-apps/<cluster>/<application>.yaml` 的同一 alias 上增加：

```yaml
metadata:
  annotations:
    argocd-image-updater.argoproj.io/image-list: <alias>=<literal-harbor-repository>
    argocd-image-updater.argoproj.io/<alias>.update-strategy: newest-build
    argocd-image-updater.argoproj.io/<alias>.allow-tags: "regexp:^[a-f0-9]{7,40}$"
    argocd-image-updater.argoproj.io/<alias>.force-update: "true"
```

只使用 alias 级 `force-update`，不要扩大成 Application-wide 开关。提交 MR 并通过
`argocd-apps` validator；不要直接 patch live Application，因为上层 root app 会覆盖。

合并后验证完整链路：

```bash
# 1. root app 已把 annotation 下发
kubectl --context "$CTX" -n argo-cd get application "$APP" \
  -o jsonpath='{.metadata.annotations.argocd-image-updater\.argoproj\.io/<alias>\.force-update}{"\n"}'

# 2. Image Updater 已向 live Application 写入新 SHA
kubectl --context "$CTX" -n argo-cd logs deploy/argocd-image-updater-controller --since=20m \
  | rg "$APP|Setting new image|Successfully updated live application spec|images_updated"

# 3. Argo CD 与工作负载健康，Pod 实际镜像已变化
kubectl --context "$CTX" -n argo-cd get application "$APP"
kubectl --context "$CTX" -n "$NS" get rollout "$WORKLOAD" -o wide 2>/dev/null \
  || kubectl --context "$CTX" -n "$NS" rollout status deployment "$WORKLOAD"
kubectl --context "$CTX" -n "$NS" get pods -l app="$WORKLOAD" \
  -o custom-columns=NAME:.metadata.name,IMAGE:.spec.containers[*].image,IMAGE_ID:.status.containerStatuses[*].imageID
```

## 为什么不走其它方案

- **把 `image-list` 固定成某个 SHA + digest**：只修当前一次发布，后续新 SHA 不再自动发现，破坏 self-service contract。
- **改用 `latest`**：Pod 最终运行必须是 Git SHA 或语义化版本；浮动 tag 破坏追溯和回滚。
- **让 CI 每次改 `argocd-apps`**：当前契约已经由 Image Updater 自动写回，不要先引入第二套发布机制。
- **改成 Git/MR write-back 或引入 Flux**：会增加 Git 权限、CR、merger 或第二个 owner，
  不能修复 Argo CD resource tree 根因。
- **先重启 Image Updater**：进程重启不能让缺失的 `status.summary.images` 出现；若 test 已正确选镜像，先修 discovery gate。
- **Application-wide `force-update`**：会扩大到所有 alias；alias 级开关也只限有证据的目标，
  并必须记录 no-change 周期是否重复写 live override。

## 参考

- Image Updater images 配置：`<image_alias>.force-update` 用于更新未被识别为当前部署的镜像：
  https://argocd-image-updater.readthedocs.io/en/latest/configuration/images/
- Image Updater 更新流程：controller 先判断镜像是否实际部署，再查询 registry：
  https://argocd-image-updater.readthedocs.io/en/stable/basics/update/
- `SKILL.md` Rules #8/#37：`newest-build` + Git SHA allow-list +
  `write-back-method=argocd` + Application recovery seed。
