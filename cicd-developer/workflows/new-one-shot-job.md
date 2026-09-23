---
name: new-one-shot-job
description: 注册一次性、有副作用的 Kubernetes Job。注册 MR 必须保持 suspend=true；实际执行使用独立审批记录和独立 MR。
---

# Workflow：new-one-shot-job

## 目的

为数据复制、一次性回填、受控清理等有限批处理任务生成 GitOps 清单，同时保证
“合并注册清单”不会等于“立即执行任务”。本 workflow 只产出注册状态：

- Job 固定 `spec.suspend: true`；
- `ops.addx.io/execution-approval: pending`；
- 不使用 Argo CD hook；
- 不设置 TTL，避免 Job 完成后被删除并由 self-heal 重建、重复执行；
- 实际执行必须由后续独立 MR 同时写入 GitLab issue approval note URL 并将
  `suspend` 改为 `false`。

CronJob、周期任务和常驻 worker 不属于本 workflow。命中这些意图时 STOP，保留
`new-cronjob` 独立路由，不得把周期任务伪装成一次性 Job。

## Step 1. 解析目标并锁定 GitOps owner

[precondition]
  - 用户明确任务是有限、只执行一次的 batch；如只说“服务”且无法从既有 runbook
    证明 one-shot，STOP 问清楚
  - `docs/deployment/cd-requirements.md` 已存在；不存在时先完整执行
    `workflows/interview-cd-requirements.md`

[action]
  - 用 `references/data/env-keywords.yaml` 解析 `$target.env_keyword`、cluster 和
    namespace；keyword 不存在立即 STOP
  - 读取现有 Argo CD Application，确认 source path、destination namespace、
    auto-sync/selfHeal、当前 revision 和资源 owner
  - 把 Job 放进 owning application repository 的现有 overlay；不得新建第二个
    Application 接管同 namespace
  - 现有 namespace 若是 hard-rules #31 的精确 grandfather 身份，只允许复用该
    Application 的既有 path/namespace，任何 sibling 不继承例外

[validate]
  - cluster、namespace、Application、repository、overlay 五项都有 live/Git 证据
  - overlay 由现有 Application 管理；无新 Application、无 namespace 接管

[output]
  - `$target`、`$application`、`$overlay`

## Step 2. 固化副作用与执行审批契约

[precondition]
  - Step 1 完成

[action]
  - 在现有 migration runbook 或 cd-requirements 记录：
    1. 精确副作用和允许修改的对象
    2. 执行前 hard-stop 条件
    3. 成功验收证据
    4. 失败后的恢复/回滚路径
    5. 执行 issue 和审批人
  - 锁定三阶段 MR：
    1. prerequisite MR：Secret、网络、镜像和脚本准备完成
    2. registration MR：创建 `suspend: true` Job
    3. execution MR：只写 approval note URL 并切 `suspend: false`
  - execution MR 不得顺带修改 script、image、Secret 引用、资源或网络；任何此类变化
    退回 prerequisite/registration review
  - cleanup 使用独立 MR 删除已完成 Job；不得用 `ttlSecondsAfterFinished`

[validate]
  - registration MR 与 execution MR 分离
  - execution approval 使用完整 GitLab issue note URL，不能写姓名、`approved` 或
    自由文本代替
  - rollback 不依赖重试同一个有副作用 Job

[output]
  - 文档中的 `One-shot Job execution contract`

## Step 3. 验证运行输入

[precondition]
  - Step 2 完成

[action]
  - 选择已存在于内部 Harbor 的固定 tag/digest image；禁止 floating tag
  - script 必须 `set -eu` 或等价 fail-closed，并满足：成功退出 0、失败非 0、日志不
    输出凭据、每个 destructive step 前重复检查 hard-stop
  - 只引用已经 Ready 的 ConfigMap/Secret；不要在 execution MR 创建或改 Secret
  - 有数据库连接时只检查 Secret key 名，不读取或打印值
  - 明确 `activeDeadlineSeconds`、CPU/memory/ephemeral-storage requests+limits、
    scratch 和 `/tmp` `emptyDir.sizeLimit`
  - 默认 `serviceAccountName: default` 与 `automountServiceAccountToken: false`；需要
    cloud identity 时必须先证明现有 SA/IRSA/WIF 契约并单独 review
  - 需要跨 VPC/账号私网连接时，按 ops-guardrails §2D 验证目标 NodePool 每张子网
    route table 的 peering route parity，再选择 `nodeSelector`
  - 目标是 prod 时逐项检查 `references/cost-tiering/_global.yaml ->
    prod_self_check`；与一次性 Job 不适用的 CR/存储项必须记录 `NOT_APPLICABLE` 和原因，
    不能静默跳过

[validate]
  - image 可拉取且 tag/digest 固定
  - 所有引用对象已 Ready
  - script 已 review，且 hard-stop、deadline、resources、scratch size 均有确定值
  - prod_self_check 每项为 DONE 或带理由的 NOT_APPLICABLE

[output]
  - `$job_inputs`

## Step 4. 写注册清单

[precondition]
  - Step 3 完成

[action]
  - 默认用 `recipes/k8s/suspended-one-shot-job.yaml.tmpl` 填槽并写到 owning
    overlay：`one-shot-<purpose>-job.yaml`
  - 只有固定 digest 的任务镜像没有 shell/文件中转工具，或产物必须在任务容器退出后
    继续可取时，才使用
    `recipes/k8s/suspended-one-shot-artifact-handoff-job.yaml.tmpl`。不得临时自创
    initContainer/sidecar 形态：
    - `artifact-stage` initContainer 负责导入时的有界接收和 checksum 校验；导出时
      只准备空目录并立即退出
    - `run` 使用任务镜像的原生 entrypoint/command，不要求镜像带 shell
    - `artifact-relay` 在 `run` 退出后保留产物，等待 operator 通过 Kubernetes API
      流式读取并写入 acknowledgement marker，再有界退出
    - 三个容器使用同一个已验证 non-root UID、同一个 bounded `work` emptyDir 和固定
      digest image；helper script 仍由 app-scoped ConfigMap 生成
    - stage/relay script 必须有 deadline、临时文件原子 rename、SHA-256、ready/ack
      marker；operator 必须重查 context/namespace、Job UID、Pod owner UID、容器状态
      和 image digest，禁止用 Pod 名或 mutable tag 作为身份
  - 把已 review 的 script 写到同目录；通过该 overlay 的 `configMapGenerator.files`
    生成 app-scoped ConfigMap
  - 把 Job 加到 `kustomization.yaml resources`
  - 保持模板安全默认值：
    - `suspend: true`
    - approval annotation = `pending`
    - `backoffLimit: 0`、`completions: 1`、`parallelism: 1`
    - no Argo hook、no TTL
    - no service-account token、read-only root、drop ALL capabilities
    - 显式 imagePullSecrets、CPU/memory/ephemeral-storage resources、deadline 和
      emptyDir size limits
  - 更新 migration runbook/cd-requirements，写明 registration merge 不执行

[validate]
  - Job 与 script 文件存在；artifact handoff 变体的 stage/relay 两个 script 都存在
  - `kustomize build "$overlay"` 成功
  - rendered Job 仍为 `suspend: true`，approval 为 `pending`
  - rendered Job 没有 `argocd.argoproj.io/hook` 和 `ttlSecondsAfterFinished`
  - artifact handoff 变体准确保留 `artifact-stage`、`run`、`artifact-relay` 三个身份，
    三者均为 non-root/read-only-root/drop-ALL，且共享的每个 emptyDir 有 sizeLimit

[output]
  - `$overlay/one-shot-<purpose>-job.yaml`
  - `$overlay/<purpose>.sh`
  - artifact handoff 变体额外输出 `$overlay/<purpose>-stage.sh` 和
    `$overlay/<purpose>-relay.sh`
  - 更新后的 `$overlay/kustomization.yaml`
  - 更新后的 runbook/cd-requirements

## Step 5. 运行确定性门禁

[precondition]
  - Step 4 完成

[action]
  - `python3 "$skill_root/validators/check_one_shot_job.py" "$overlay"`
  - `bash "$skill_root/validators/validate.sh" --repo-context app k8s/`
  - 对准确 Application `spec.source.kustomize.images` recovery seed 做最终 Kustomize render；确认 Job image 未被
    unintended transformer 改写
  - 对目标 cluster 执行 server-side dry-run；这是 schema/admission 校验，不 apply

[validate]
  - 所有命令退出 0；任一非 0 立即 STOP 并原样报告
  - 最终 render 与 server-side dry-run 都保持 suspended registration 状态

[output]
  - validator 和 dry-run 日志

## Step 6. 提交 registration MR

[precondition]
  - Step 5 全绿

[action]
  - 只 stage 本 workflow 产生的文件
  - commit body 只用 ASCII
  - 创建 Draft registration MR；描述包含副作用、hard-stop、验证证据和明确声明
    “merge does not execute the Job”
  - MR 合并后等待 Argo CD 自动 reconcile；只读验证 Application 必须为 `Synced`，
    不得为了改变 health 状态手动 sync
  - Application health 可以是 `Healthy`；也可以是由该 Job 的 suspended condition
    唯一导致的 `Suspended`。接受 `Synced/Suspended` 前必须同时确认：
    - Job `spec.suspend: true`，approval annotation 为 `pending`
    - Job condition 为 `Suspended=True`、reason 为 `JobSuspended`
    - Job active/succeeded/failed 计数均为 0，关联 Pod 数为 0
    - 没有其他 `Degraded`、`Missing` 或异常资源
  - 把 live UID/generation 和 suspended 状态回填 execution issue

[validate]
  - registration MR pipeline 通过
  - Application 为 `Synced`；若 health 为 `Suspended`，已证明它只来自预期的挂起 Job
  - live Job 已注册但没有 Pod、没有副作用

[output]
  - registration MR URL
  - execution issue evidence note URL

## 出口

本 workflow 到 registration verification 为止。后续执行必须收到用户对准确 Job、
窗口、issue approval note 和影响范围的明确确认，再创建只改两处的 execution MR：

1. `metadata.annotations["ops.addx.io/execution-approval"]`: `pending` → 完整 issue note URL
2. `spec.suspend`: `true` → `false`

执行后记录 Job/Pod UID、节点、image digest、退出码、开始/结束时间和 task-specific
验收证据；不记录 Secret 值。失败时禁止自动重试，保持下游 writer/traffic gate 关闭。
