# 同仓按资源职责拆分 Application

这是新建或修正部署方案的指导，不自动修改已有资源、Project 或集群。业务使用目标集群已批准的
共享 `app-runtime` 和共享 `app-data-plane`，不创建按业务 owner/component 命名的 Project。
Model Serving 的 owner 专属 Project 已通过 [argocd-apps !2221](https://gitlab.addx.ai/DEV/argocd-apps/-/merge_requests/2221)
上线；它是待单独评审纠正的例外，不能称为未上线提案，也不能称为已经回迁。

## 先登记两个准确合同，再写资源

每个 target 记录 runtime 和 infra 各自的 Application 名、源仓库、targetRevision、验收用固定源 SHA、独立渲染路径、
全部 values/参数、destination、已批准的 Project 与资源清单。下文变量只是已核验合同的别名：

- `$target.runtime_source_path`：现有 `k8s/overlays/{$target.env_keyword}` 或获批的 runtime 入口。
- `$target.infra_source_path`：同仓独立 infra 入口，例如 `k8s/infra/{$target.env_keyword}`；示例不自动授权路径。
- runtime → 共享 `app-runtime`；获批 raw RDS/Aurora、ElastiCache、S3 等 → 共享 `app-data-plane`。
  CloudFront/NineData 等也必须核对准确目标允许的 kind，不能从分类示例推断已授权。

必须先明确这两个入口的准确合同；缺少 infra 合同就交付平台准备 Ops Todo，不把 raw 资源
塞入 runtime 或把混合 Application 整体迁入数据面，不虚构路径/Project 以继续注册。
`Application.spec.project` 只有一个；多 sources 也不会按资源类型自动分派 Project。
共享 Project 不是任意业务可复用其全部权限的授权，repo/path/revision/identity 仍按准确 CI 合同审批。

## 资源落位与独立渲染

| 资源 | 管理入口 |
|---|---|
| Deployment/Rollout、Service、Ingress、运行配置与消费凭据的 ExternalSecret | runtime |
| raw RDS/Aurora/ElastiCache/S3/CDN 等已批准数据面 CR | infra |
| DB 的 A1 Password Generator、生成密码 ExternalSecret、密码/连接 PushSecret | 与 raw DB 同一个 infra Application |
| 平台 Database/ObjectBucket 等高层 claim | 按现行精确产品合同，可保留 runtime；不因 Crossplane API 名称强制拆分 |
| IAM/IRSA、ProviderConfig、WAF、共享网络权限 | 平台 `crossplane-infra` 入口，不迁入业务仓 |

producer 和 consumer 的 ExternalSecret 按用途归属，不能只按 kind 机械切分。
Kustomize 类型的每个源路径有自己的 kustomization；runtime root 不引用 infra kustomization、文件或输出。
本轮 raw 模板工作流只自动生成/登记 Kustomize 入口（标准或获批自定义目录）；
非 Kustomize 入口必须先明确独立的写入、资源登记、参数、构建与验证适配方案并评审，
不能仅替换下文 build 命令就宣称 workflow 已支持该渲染器。
所有 workflow 的 infra `resources` 登记、构建和校验都指向 `$target.infra_source_path`，
runtime consumer 指向 `$target.runtime_source_path`。仓库级扫描可保留，但不能代替两份独立 render。
固定全部输入后比较 cluster/group/kind/实际 namespace/name：资源不重复、不遗漏，operator 派生对象
按 controller ownership/tracking 归因。字段准入、providerConfigRef 和云 IAM 继续独立校验。

### 两份 render 的执行与验收

raw 分支每次生成或修改后，在登记的同一组固定 SHA/参数上分别执行：

```bash
runtime_render_dir="$(mktemp -d)"
infra_render_dir="$(mktemp -d)"
kustomize build "{$target.runtime_source_path}" > "$runtime_render_dir/manifest.yaml"
kustomize build "{$target.infra_source_path}" > "$infra_render_dir/manifest.yaml"
bash "$skill_root/validators/validate.sh" --repo-context app "$runtime_render_dir"
bash "$skill_root/validators/validate.sh" --repo-context app "$infra_render_dir"
```

这里路径槽位必须先替换为已登记路径，不能直接执行未替换文本；任一步非零即停止。
若合同含额外 render 参数，必须原样带入；不是 Kustomize 的已批准入口使用其冻结的
实际渲染命令，不能为套用示例更换入口。需要 Database identity inventory、ObjectBucket
能力证明等额外 gate 的资源继续执行原门禁，不能以这两个基础 validator PASS 替代。

将两份最终 render 与修改前清单逐项按 cluster/group/kind/实际 namespace/name 对账：
交集为空、并集等于本次批准的预期资源（新建资源显式登记，既有资源不得静默消失）。
同时核对本 workflow 的关键输出与引用，例如 consumer 的 Secret keys/remoteRef、
workload 的消费引用、CDN 配置值、DataSource 的连接 Secret；文件存在不代表已被渲染。
只改 infra 的 workflow 也构建 runtime 并记录其预期不变，不为凑两份变更添加 runtime 资源。
此对账是执行者提供的证据，不声称现有 validator 已自动覆盖跨 Application 互斥。

shared-only 的高层 claim 分支仍执行其原 runtime render 与能力/identity gate，不要求创建
不存在的 infra 入口，也不把平台 producer 纳入业务 runtime；混合 target 按各自合同分别验证。

## 依赖、验收与迁移

资源 sync-wave 只在所属 Application 的同步中排序。A1 生产链与 DB 在 infra 内保留现有 wave；
runtime 的 ExternalSecret/workload wave 不证明另一 Application 已完成。先验收 infra 的 Secret、
DB Ready/Synced、连接 PushSecret 与 Vault 交付，再验证 runtime 消费 Secret 的必需 keys、
迁移作业和 workload。不得把 workflow 步骤、等待状态或 MR 合并当作 live 操作授权。
若采用 App-of-Apps child wave，还须证明 live Application health gate 和基础设施实际 Ready 条件；
仅给两个 child 排 wave 不算跨应用就绪证据。

新服务 raw 基础设施先通过独立 infra Application 准备/验收，然后按原镜像证据门禁交付 runtime
Application。原 new-service 单 runtime 注册步骤不自动生成 infra Application；缺少其独立合同、
交付或就绪证据时记录精确 handoff，不宣称部署完成。shared claim 的原自助流程保持不变。

已有资源不直接移动文件然后等待 prune。另行评审逐资源唯一管理者、保留/删除保护、暂停旧写入、
tracking 接管、无遗漏/重复的前后 render 与 live UID，以及正反交接和回滚窗口。纯同职责 project-only
迁移可另行评审，但混合资源拆分不能用单行 `spec.project` 回滚替代所有权恢复。
本指导不声称现有 validator 已实现通用跨 Application 互斥或依赖状态机。
