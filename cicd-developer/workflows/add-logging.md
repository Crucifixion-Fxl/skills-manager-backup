---
name: add-logging
description: 给应用接入平台日志采集（pod template 加 `app` + `env` 两个 label）。新部署默认接入；现有应用补接入或验证时，按目标 collector 的 direct ES/OpenSearch 或 Kafka/consumer 链路验收。
---

# Workflow：add-logging

## 目的与范围

本 workflow 只修复或验证现有 workload 的 `spec.template.metadata.labels`，
不新建日志平台资源。平台链路可能是 fluent-bit → Kafka → Vector/consumer → ES，
也可能是 fluent-bit 直写 OpenSearch/ES。先读 `references/logging/README.md`，
按目标 collector 配置决定检查项；缺少不属于该链路的组件不是故障。

新应用走 `new-service.md` / `new-stateful-service.md`，默认模板已包含 label。
旧应用补接入、迁移到现有采集栈或显式 audit / E2E 使用本 workflow。
平台前置由 ops 管理；用户要求 ES 入库验证时，必须查到该 app 的近期文档，
不能用“label 正确”或“共享 index 存在”代替 PASS。

## 进入条件

- 工作目录是应用 Git 仓库，已有 `k8s/base/` 与至少一个 `k8s/overlays/<env_keyword>/`。
- 已知目标 app 与 env keyword；按 env 循环处理。
- 解析 `data/env-keywords.yaml` → `data/clusters.yaml`，Build 写入前确认
  `deployment_status: allowed`。历史 lookup 不能绕过集群准入门禁。
- Review / Scan 只检查并报告，不进入本 workflow 的修改步骤。
- `$delivery_phase` 为 `candidate` 或 `runtime`，独立调用默认 `runtime`。
  `new-service` 的首次部署候选阶段传入 `candidate`：先完成离线标签验证，
  把采集和入库验收登记为 `pending_runtime_checks`，由父流程首次同步后恢复。

## Step 1. 探测 workload 与实际链路

[precondition]
  - 目标 app、environment、cluster 和 namespace 已解析

[action]
  - 跟随目标 overlay/base Kustomization 的 resources 找到实际 workload 文件；
    不依赖文件必须叫 `rollout.yaml` / `statefulset.yaml` / `deployment.yaml`。
  - 记录 workload 的 kind、name、源文件，以及渲染后的 pod-template app/env label。
    支持 Rollout / StatefulSet / Deployment；其他 kind STOP，转对应 workflow。
  - 保存修改前完整 render；沿 overlay/base/components 检查 `patches`、`labels`、
    `commonLabels` 和显式 `LabelTransformer`，记录每个 app/env 的最终写入来源。
    同时保存 selector、其他标签和非目标资源，供 Step 5 对比。
  - label 已由合法 patch 或 transformer 注入时，不要求重复添加 patch。
  - 查目标 Application 对应的 collector source/valueFiles，再查实际 input/filter/output。
    记录 `$log_topology`（direct 或 kafka-consumer）、index/alias 查找方式、
    `$uses_business_namespace_gate` 与 `$namespace_gate_result`。
  - 只有 collector 调用共享 Lua 时，才按 business namespace gate 判断：`default`
    或 `staging-` / `pre-` / `prod-` / `canary-` / `test-` 前缀放行。
    不调用该 Lua 时检查其实际 Exclude_Path/filter，不套用共享 gate。
  - 无法读取平台配置时，保留 `topology: NOT_VERIFIED`；仍可完成 label 的离线修复，
    但不能输出日志入库 PASS，也不能猜测 Kafka / Vector 故障。

[validate]
  - 每个目标 workload 身份和标签来源明确
  - 不通过修改 namespace、selector 或平台白名单来绕过日志检查

[output]
  - 每 env 的 workload、标签状态、topology、filter/gate 结果和证据来源

## Step 2. 决策

[precondition]
  - Step 1 完成

[action]
  - 渲染结果已有正确 app/env → 跳过修改，进 Step 5。
  - 缺 app → Step 3；缺 env 或值不符 → Step 4。
  - Deployment 使用已有、获准的 deployment exception 时可继续补标签；若当前
    平台规则要求 Rollout 且没有已获准 exception，STOP + Ops Todo，不能在日志改动中擅自换 workload。
  - StatefulSet 和 Deployment 是 Kubernetes 内置资源；Rollout 是 CRD。
    先修正实际标签来源；没有后续 transformer 覆盖时才使用精确 pod-template patch。
  - 若待修正的 app/env 同时参与 workload 的 selector 且值与目标值冲突，STOP +
    Ops Todo，另行设计 selector/工作负载迁移；不能生成 selector 与 pod 标签不匹配的候选。

[validate]
  - 修改范围仅为目标 workload 的标签；已正确的标签不重写

[output]
  - 每 env 的修改计划或只读发现

## Step 3. 补 app label（如需）

[precondition]
  - Step 2 确认 app label 缺失

[action]
  - 在已定位的 workload 源文件 `spec.template.metadata.labels` 加入
    `app: <canonical-app-name>`。canonical app 来自应用/路由合同，不从 cluster 名推导。
  - 共用 base 只修改一次；检查对其他 overlay 的影响，不改 selector。

[validate]
  - Kustomize 仍可构建；未改动其他 workload 或 selector

[output]
  - 必要的 pod-template app label 改动

## Step 4. 补 env label（如需）

[precondition]
  - Step 2 确认 env label 缺失或值不符

[action]
  - 先定位 Step 1 记录的最终写入来源。内置 workload 的 `labels` / `commonLabels`
    或显式 `LabelTransformer` 可能在 patches 之后写入标签；继续叠加 patch 无法修复覆盖。
    若该来源只写目标 pod template，直接修正其 env 值，并保留其他键和 fieldSpecs。
  - 若来源还写 resource metadata、selector 或其他 workload，拆分其中的 env 注入，
    保留这些位置的原值，仅取消目标 pod template 的错误写入，再加下面的精确 patch。
    例如单个目标 workload 使用 `labels`、`includeTemplates: true` 且没有 env selector 时，
    将 env 拆到 `includeTemplates: false, includeSelectors: false` 的独立项，其他标签
    保留原配置。修改 `commonLabels` 或共享 transformer 时同样保持其他位置的渲染结果；
    无法在当前 overlay 隔离影响则 STOP，不直接修改共享 base 影响其他环境。
    下例保留原 resource metadata 的 `env: staging` 和其他标签，仅修复 pod env：
        ```yaml
        # logging-env-source-repair
        labels:
          - pairs:
              team: backend
            includeTemplates: true
            includeSelectors: false
          - pairs:
              env: staging
            includeTemplates: false
            includeSelectors: false
        ```
  - 确认没有剩余的错误 transformer 写入后，在对应 overlay 的 patches 中修改既有 patch，
    或增加精确 kind/name target：
        ```yaml
        # logging-env-pod-patch
        patches:
          - target:
              kind: <workload-kind>
              name: <workload-name>
            patch: |-
              - op: add
                path: /spec/template/metadata/labels/env
                value: <env-keyword>
        ```
  - 保留已有 labels；必要时先创建空的 `spec.template.metadata.labels` map，
    不能用替换整个 map 的办法丢掉其他标签。
  - Rollout 不依赖顶层 Kustomize labels transformer；StatefulSet / Deployment
    已有合法标签注入方式时只验证渲染值，不因缺少 patches 字段而重复修复。

[validate]
  - YAML 可解析；target 只选中目标 workload；无 selector 变更或 selector/pod 标签冲突

[output]
  - 每 env 必要的 pod-template env label 改动

## Step 5. 验证

[precondition]
  - Step 3-4 完成或无需修改；`kustomize` 与 Python/PyYAML 可用

[action]
  - 每 env 构建完整 YAML stream，再结构化检查标签，不靠相邻行 grep：
        ```bash
        rendered="$(mktemp -t cicd-v2-rendered.XXXXXX.yaml)"
        trap 'rm -f "$rendered"' EXIT
        kustomize build k8s/overlays/<env_keyword>/ > "$rendered"
        if command -v yq >/dev/null 2>&1; then
          yq 'select(.kind == "Rollout" or .kind == "StatefulSet" or .kind == "Deployment") | .spec.template.metadata.labels' "$rendered"
        fi
        python3 - "$rendered" <<'PY'
        import sys
        import yaml
        count = 0
        with open(sys.argv[1], encoding="utf-8") as fh:
            for doc in yaml.safe_load_all(fh):
                if not isinstance(doc, dict) or doc.get("kind") not in {"Rollout", "StatefulSet", "Deployment"}:
                    continue
                count += 1
                labels = (((doc.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("labels") or {}
                name = (doc.get("metadata") or {}).get("name")
                assert labels.get("app") and labels.get("env"), f"{doc['kind']}/{name}: missing app/env"
                print(doc["kind"], name, labels)
        assert count, "no supported workload found in rendered YAML"
        PY
        ```
  - 核对输出中目标 workload 的值精确等于 app 和 env keyword；不只检查 key 存在。
    任一缺失或错误 → STOP，修复后重新构建。
  - 对比 Step 1 的完整 render：selector、其他 pod 标签、resource metadata 标签和非目标
    资源保持不变；共用 base/source 有改动时也构建受影响的其他 overlay。检查 selector
    仍匹配 pod 标签；不能只看 source patch 或把 transformer 覆盖当作验证完成。
  - `candidate`：完整 render 与下方 manifest validator 均通过后，记录
    `cicd.md` 的 `$pending_runtime_checks[]`：`target` 为准确 app/env/cluster/namespace，
    `workflow=add-logging.md`，`resume-step=Step 5：live collector/index 验收`，
    `resource-or-secret` 记录 workload、collector 和已知 index/alias 身份，
    `acceptance` 为 collector 覆盖该节点、正确链路且查到该 app 的近期日志，`status=pending`。
    经 Step 6 汇总后返回父流程；恢复时重新解析实际 topology，不把旧的未知配置当事实。
    首次同步前没有新 app 日志不阻断候选；此状态不等于 E2E PASS。
  - 显式 E2E/audit：目标实际被 filter drop 时报告该 filter；否则检查 collector Ready、
    该节点覆盖和相应 direct 或 kafka-consumer 下游，再查该 app 的近期文档。
    该项仅在 `runtime` 执行；恢复首次部署验收时也必须完成。
  - 不新建测试资源、改平台配置或读取凭据来补足本 workflow 的权限。

[validate]
  - `bash "$skill_root/validators/validate.sh" k8s/overlays/<env_keyword>/` PASS
  - 目标 app/env 渲染值正确，selector 保持不变
  - 离线 manifest 验证与 live E2E 结论分别记录；缺证据不能输出 E2E PASS

[output]
  - 渲染验证和所获准的只读平台证据

## Step 6. Summary + Ops Todo

[precondition]
  - Step 5 完成

[action]
  - 每 env 说明 workload 标签结果、collector topology/filter，以及实际 index/alias。
    Kafka topic 只在 Kafka output 链路报告；direct output 没有 Kafka hop。
  - source 配置未知 → `NOT_VERIFIED`；实际链路前置缺失 → `BLOCKED` / Ops Todo；
    namespace 确实被 filter drop → `NOT_APPLICABLE` 或相应 blocker。
    `candidate` 单独报告离线结果与待恢复的运行态检查，不把尚未执行的检查报为 PASS。
  - label 随获准的 GitOps 发布和 workload 更新生效；不要保证固定分钟数内入库。
  - JSON stdout 建议以目标 parser 为准；日志入口和查询权限按集群配置，不承诺
    所有人能创建 index pattern，也不直接编辑 `.kibana` 系统索引。

[validate]
  - 没有把缺少无关 Kafka / Vector 误报为 direct 链路 blocker
  - 没有用离线配置代替线上健康或入库证据

[output]
  - 最终结果与确切的剩余平台检查

## 关联

- 平台链路：`references/logging/README.md`
- 排障：`troubleshooting/log-pipeline-silent-loss.md`
- 环境：`references/data/env-keywords.yaml`
- 标签模板：`recipes/k8s/rollout.yaml.tmpl` / `recipes/k8s/kustomization-overlay.yaml.tmpl`
