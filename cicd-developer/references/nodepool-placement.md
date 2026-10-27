# 应用接入 NodePool

用于新服务、新目标 overlay、Job/exporter，以及已授权的存量调度变更。它是应用
PodSpec 的接入合同，不是创建 NodePool、修改云网络或执行生产迁移的授权。
先按当前 workflow 确定允许修改的资源；没有匹配的 Build route 时仍保留 Ops Todo。

## 规范来源与边界

- [Fleet 整理准则](https://gitlab.addx.ai/DEV/k8s/-/blob/master/docs/runbooks/nodepool-consolidation-standard.md)
- [私有 NAT 收敛方案](https://gitlab.addx.ai/DEV/k8s/-/blob/master/docs/plans/2026-09-09-us-prod-shared-private-nat-migration.md)
- [Stateful 架构分层](https://gitlab.addx.ai/DEV/k8s/-/blob/master/docs/runbooks/nodepool-stateful-architecture.md)

核对基线：`DEV/k8s` commit `7bee4406c745e9f31925edeaf0a3d7598637f7f3`。
Fleet 准则中“需要 NAT 就选 nat-egress”的历史措辞不能用于把所有业务合进同一计算池；
本轮 operator 已确认：所有应用默认使用私有子网和稳定 NAT，不限于 shared/platform/stateful；
只有明确的特殊要求才允许按第 4 节记录网络例外。计算池用途与网络选择分别判断。
该目标不证明全 fleet 存量已完成迁移，具体接入以目标集群当前声明及 live 能力为准。

## 1. 选择已有能力池

按用途隔离、CPU 架构、容量类型、网络出口分别判断；不要按应用名或环境名新建池，
也不要把所有维度机械做笛卡尔积。普通应用默认共享，例外需要可核验的隔离理由。

| 负载 | 默认目标 | 条件 |
|---|---|---|
| 普通服务，保守迁移或不能承受抢占 | `shared-amd64-ondemand` / `shared-arm64-ondemand` | 选择已验证镜像架构 |
| 可中断、可重建的无状态负载 | `shared-amd64-spot` / `shared-arm64-spot` | 明确接受抢占，具备恢复能力；不能只因成本低就选择 |
| 持久数据、quorum、关键单副本/状态恢复需求 | `stateful-amd64-ondemand` / `stateful-arm64-ondemand` | 已启用供给、对应架构镜像、存储/拓扑与恢复合同 |
| 平台控制面 | `cicd-system` / `crossplane-providers` | 仅登记的控制器/provider；普通 API、exporter 或使用云凭据的业务不因此获得准入 |
| GPU、构建/高 churn、合规硬隔离 | 已批准的能力池 | 写清消费者、隔离理由、taint/toleration、网络及回滚 |

既有 `stateful-ondemand` 保持现状，不能批量改名或切架构。分架构池可能仍是
`limits.cpu: "0"` 的预备池；创建成功、Ready 或名称存在均不证明能接应用。
有状态消费者匹配 `dedicated=stateful:NoSchedule`，并核对实际 taint；不加通配
`Exists` toleration。是否有状态按数据生命周期判断，不能只看 kind/PVC，或擅自把
`emptyDir`、PVC、恢复方式互相转换。

存量 NodePool 整理保留原架构、capacity-type、镜像及业务语义。Spot 化、扩容、
架构转换、探针调整和数据迁移分别评审。prod `iot-service` / `iot-consumer`
（含 prod-us/consumer 及专用/灰度池）是明确 no-touch 例外，不纳入普通规范化。

## 2. 生成前核验精确目标

在部署需求/迁移记录中保存 `cluster/context、namespace/workload、Git/Argo owner、
目标 pool、架构、capacity、网络、taint/toleration、证据时间`。通过已有凭据只读核对：

1. 目标池和 NodeClass 的 Git 来源、当前 Ready、允许架构/容量、可用供给额度、
   子网/AZ/IP 与实例供给条件。零节点可按需供给，不必人为扩容验名；零供给额度须先解决。
2. 镜像 manifest 覆盖所有允许架构，现有 affinity/selector/toleration 不相互矛盾。
   `build_mode=dual-arch` 仍要求双架构构建，但不代表可以不限制节点池或默认跑 Spot。
   单池部署可以使用 multi-arch image；存量单架构不因本次整理被强制升级。
3. 核对真实数据/卷、PV nodeAffinity 与目标 AZ、requests 与节点 allocatable、
   副本分布、PDB/拓扑约束及启动/恢复能力。不能把所有新服务固定低 requests 当容量依据。
4. 平台 seed/bootstrap 或 provider-managed add-on 按实际 owner 配置，不把 Karpenter
   自身强制迁到其管理的池，不以 live patch 替代持久来源。

缺少目标能力时记录具体前置条件；应用接入不能顺带创建池、扩大 limits、去 taint，
也不能临时退回未批准平台/业务专用池。

## 3. PodSpec 的调度约束

EKS/Karpenter 使用 `karpenter.sh/nodepool`；禁止新写 `node-group` selector。
只写 arch/capacity 或 preferred affinity 不能阻止业务落入未加 taint 的专用池。
placement 放在环境 overlay/owner values 中，跨云共用 base 保持可移植；最终 render
必须含完整约束。以下是现有 workload recipe 的 PodSpec placement 变体，不创建新 kind。

已提供 private-nat template label 的目标（例如 US Prod 本轮共享池），单池采用
全部相等约束；网络标签只标新私有供给，不能回填到旧公网节点：

```yaml
nodeSelector:
  kubernetes.io/arch: amd64
  karpenter.sh/nodepool: shared-amd64-ondemand
  karpenter.sh/capacity-type: on-demand
  network.addx.io/egress: private-nat
```

ARM 使用相应 `arm64` 和池名；不能只改其中一个字段。NodeClass 在 NodePool
`spec.template.spec.nodeClassRef` 及实际节点中核验，**不要**把
`karpenter.k8s.aws/ec2nodeclass` 加进 Pod selector/required affinity：实际节点有这个
标签不代表它是 Karpenter 可用于新节点供给的 Pod requirement。US Prod 的
[共享池声明](https://gitlab.addx.ai/DEV/k8s/-/blob/7bee4406c745e9f31925edeaf0a3d7598637f7f3/clusters/aws-302571458622-us-prod/karpenter/shared-amd64-ondemand.yaml)
已明确使用 network label 而非 class label；不能仅在现有节点上能调度就判供给有效。
其他集群必须先查实际 pool→class 和网络标签合同。尚未发布 label 的目标不照抄此例产生
Pending；记录平台前置条件或使用经批准、已证明等价的目标约束和实际网络证据。

确需保留双架构调度且两个目标池均已核验时，用 required affinity 的有限允许列表。
下面每个 term 内是 AND，两个 term 之间是 OR；池身份与架构要配对，容量和网络
对所有 term 生效。不能附加空 term 或只有 arch 的 OR 分支。

```yaml
nodeSelector:
  network.addx.io/egress: private-nat
  karpenter.sh/capacity-type: on-demand
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: karpenter.sh/nodepool
              operator: In
              values: [shared-amd64-ondemand]
            - key: kubernetes.io/arch
              operator: In
              values: [amd64]
        - matchExpressions:
            - key: karpenter.sh/nodepool
              operator: In
              values: [shared-arm64-ondemand]
            - key: kubernetes.io/arch
              operator: In
              values: [arm64]
```

保留既有 podAntiAffinity/topologySpreadConstraints；合并 nodeAffinity 时对所有 OR
分支都保留目标边界，不能覆盖旧约束。Spot 是另一个经批准的选择，不与上述 on-demand
允许列表暗中混用。TKE/GKE 使用经核验的 provider 原生池标识；不伪造 Karpenter/
EC2NodeClass 标签，也不假设上述 private-nat label 已存在。

## 4. 网络与名称

**所有应用在所有目标集群默认使用私有子网和稳定 NAT 出口**，包括普通服务、Job、
exporter、平台组件和专用池消费者；不是仅“当前已知要调用外部服务”的应用才需要 NAT。
专用池、Spot、云厂商不同、暂时没有外部调用或缺少 network label 都不是网络例外。
只有明确的特殊要求才可提出例外：在部署需求/MR 中记录准确 workload/target、理由、
替代网络路径和安全边界，取得 operator 的明确确认后才按例外实施；未确认则仍采用默认。
例外不自动放行动态节点公网 IP 加白或无防护公网暴露，也不授权改动存量网络/工作负载。

默认 NAT 不意味着所有应用都进入 `nat-egress` 池；保留各用途的原标准池名，
不添加 `-private`。目标池没有满足默认网络时记录平台前置条件，不能静默降级为公网节点。
实例显示名对应实际池：AWS EC2 `Name` 等于 NodePool 名，Node/hostname 不改。
其他 provider 先确认名称约束和映射；应用接入只核验并记录平台欠项，不顺带改实例名。

网络验收必须区分：标签声明、实际 subnet/route table/公网 IP/NAT 配置、Pod 实测。
跨 VPC 入站调用方和监控、出站数据库/API 的路由、回程、SG/NACL/DNS 都要匹配；
VPC local、peering 和 endpoint 流量按实际路由直达，不必绕行 NAT；这不改变默认外部出口。

默认 NAT 路径的授权运行验收中，从实际目标 Pod 执行有超时的 `curl -4 https://checkip.amazonaws.com`，
覆盖可调度子网/AZ。EKS 与目标账号/区域的 `aws ec2 describe-nat-gateways` EIP 对照；
TKE/GKE 使用对应 provider 原生 NAT 配置和固定出口 IP 证据，不套用 AWS API。
不把动态节点公网 IP 加白。镜像没有工具时如实记为未实测；未经另外授权不安装工具、
修改镜像或创建探针 Pod。已有等价证据只能在 operator 明确接受的范围复用，记录
原证据的节点/网络/时间及未实测边界，不把一次接受推广为其他应用的全局豁免。
已确认的网络例外则按记录的替代路径和安全边界收集配置及运行证据，不要求为满足默认
NAT 检查而新增公网出口；未确认或缺少替代路径证据时不能按例外通过。

## 5. 交付与验收

- 修改 Pod template 调度字段会触发 Deployment/Rollout 替换 Pod；StatefulSet/Operator
  按其策略处理。不能承诺“只改 selector 不重启”，也不能以 PDB 推导零中断。
- 提交前对准确 Argo source/path/revision 和 image override 渲染，比较全部资源；
  调度-only MR 只改变批准的 placement，检查旧 `node-group` 是否因 merge 残留。
  同时检查应用仓 CI rules 和 Image Updater，说明合并是否会构建/推镜像/自动部署。
- 生产按服务/小批次执行已批准计划，记录镜像 digest、原落点、流量/健康/业务基线和
  回滚路径。PDB、容量、可暂停窗口与持久数据恢复条件不足时不驱逐/重启。
- 合并后回读 Argo revision、同步/操作状态、workload observedGeneration、
  updated/Ready/Available 与当前期望副本数、Pod 重启数、实际 pool/class/arch/network、
  Node Ready、EndpointSlice、真实业务/采集指标及第 4 节对应网络路径的证据。HPA 正常伸缩不算异常。
  镜像不应在调度-only 迁移中无解释变化；每个验收项明确通过/待验/例外。
- 代码合并、Synced/Healthy、节点标签或 HTTP 200 只证明各自阶段。未完成运行/网络
  验收时保持该项开放；不自动关闭整个 NodePool Root Issue，不删除旧池或驱逐其余应用。

本 reference 由 workflow 和人工/Agent Review 使用，现有 manifest validators 并未完整
强制这些 NodePool/网络规则；不得把 validator PASS 当作 placement 或实际 NAT 验收。
