# 新腾讯云 CN staging：FluentBit 直写 Elasticsearch

适用目标：腾讯云账户 `100052802231`，GitOps 集群 `tencent-100052802231-cn-staging`（ap-beijing）。AWS CN 已弃用，prod 仍在腾讯云 `100014919455` / `cn-main`；不要因旧目录、旧 context 或存量 Vector 的 staging 配置就把新 staging 日志发到 prod 管道。

## 已确认的声明与证据边界

截至 2026-09-28，来源为 `DEV/k8s` 的 `master`：

- `clusters/tencent-100052802231-cn-staging/logging/fluent-bit.yaml`：namespace `logging`、DaemonSet `fluent-bit`、ConfigMap `fluent-bit-config`。
- INPUT 覆盖 `/var/log/containers/*.log`；排除 kube-system、kube-public、kube-node-lease、logging namespace。
- OUTPUT 为 `Name es`，目标 `elasticsearch.elasticsearch.svc.cluster.local:9200`，索引前缀 `addx-cn-staging`，按 `%Y.%m.%d` 分日。
- `Merge_Log Off` / `Keep_Log On`：原始业务日志保留在 `log` 字段，不假设已展开 JSON 业务字段。
- ES 与 Kibana 声明位于同集群的 `elasticsearch/` 目录。

这是仓库声明，不代表本次已验证运行态或已找到对应 ArgoCD Application。先查询 Application 的 source/destination 与资源管理标签；不能因为文件在 Git 就臆造 Application 名，也不能先直接 apply。

## 先定位，通常无需为每个应用改采集配置

1. 用 `kubectl config get-contexts -o name` 找到与此集群匹配的实际本地 context，设为 `CN_STAGING_CONTEXT`。核对目标应用 namespace、pod、container、节点及日志文件名。
2. 读取 `logging/fluent-bit-config` 的 INPUT/FILTER/OUTPUT，与上述源文件对比，核实管理归属。
3. 新应用若已被通配 INPUT 覆盖，且未被 namespace/Pod annotation 排除，先验证 ES 中是否已有文档；无需新增 Kafka topic、Vector transform 或按服务重复 INPUT。
4. 不把缺失 Kafka/Vector 判为故障：此拓扑本来就没有中间两跳。若实际 OUTPUT 不同，先回到发现流程，以现场证据为准。

只读核对命令（context 变量必须已经核实）：

```bash
kubectl --context "$CN_STAGING_CONTEXT" -n logging get ds fluent-bit -o yaml
kubectl --context "$CN_STAGING_CONTEXT" -n logging get cm fluent-bit-config -o yaml
kubectl --context "$CN_STAGING_CONTEXT" -n logging get pods -o wide
kubectl --context "$CN_STAGING_CONTEXT" -n elasticsearch get svc,pods
```

## 需要修改时的流程

- 先保存 FluentBit/ES 配置、Pod 健康、采集错误/重试和 ES 最新文档时间/写入量 baseline；不要采集 Secret 明文。
- 修改 `DEV/k8s` 对应源文件并创建 MR；确定实际 GitOps 接管点后再交付。若尚未纳管，先明确接管方式，不能绕过已有控制器。
- 仅改本次必要的 Path/排除规则、过滤或索引设置。改索引前确认检索方是否依赖 `addx-cn-staging-*`。
- 由管理该资源的控制器应用配置；ConfigMap 挂载为 subPath，配置变更后需受控滚动 FluentBit DaemonSet。不要同时启动第二套覆盖相同文件的采集器。
- 基线对比：FluentBit rollout 完成、目标节点采集 Pod Ready、无新增持续 output 错误/重试、ES 健康不劣化、现有日志写入无异常下降。失败回退本次改动。

## 直写拓扑的端到端验收

逐项保留证据，不能用 Kafka offset/consumer lag 替代：

1. 应用实际产生日志，容器日志路径匹配 INPUT，并未被排除。
2. 同节点存在健康的 FluentBit Pod，已加载目标 ES OUTPUT。
3. FluentBit 到目标 ES Service 可达，未出现持续写入失败。
4. 从集群内或已验证 Service 的 `kubectl port-forward` 查询 ES，确认 `addx-cn-staging-*` 中出现该应用 namespace/pod/container 的近期文档。先查询 mapping，按实际字段过滤，避免误读其他应用日志。
5. 样本包含预期 `log` 和 Kubernetes 元数据，时间持续推进；需要业务字段展开时另行确认解析规则。
6. 保留 rollout 后观察窗口与 baseline 对比，确认已有服务仍正常入库。未修改配置时也要完成应用→FluentBit→ES 的采集证据链。

新 `cn-tech-service` 与本集群同账户，但不因此共享日志拓扑；必须独立发现后再选择流程。
