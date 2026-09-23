# 日志接入

应用侧统一在 pod template 保留 `app` + `env` label；平台日志链路按目标集群的
collector 和下游配置确定。**不能假设全 fleet 都经过 Kafka / Vector，也不能把
label 正确当作日志入库 PASS。** 新应用的 label 由 `new-service.md` /
`new-stateful-service.md` 默认生成；本参考也供现有应用 audit / 排障使用。

## 先识别目标链路

下表是 2026-09-17 对照 Git desired state 的结果，不是线上健康证明。
先从目标 Application 的 source path / Helm valueFiles 找到实际 collector 配置，
再读 filter、output 和 frontend；集群名字中的 `staging` 不足以决定链路。

| 目标 | collector / 下游 | 索引查找方式 |
|---|---|---|
| AWS `us-eks-staging` | 共享 fluent-bit Lua filter → 直写本区 AWS OpenSearch；无 Kafka / Vector hop | 日索引 `addx-us-staging-YYYY.MM.DD`，按 pod/app 元数据过滤 |
| AWS `eu-eks-staging` | 共享 fluent-bit Lua filter → 直写本区 AWS OpenSearch；无 Kafka / Vector hop | 写 alias `addx-eu-staging`；不要假设每 app 一个日索引 |
| AWS `cn-eks-staging` | 共享 fluent-bit Lua filter → 直写 AWS China OpenSearch | 日索引 `addx-cn-staging-YYYY.MM.DD`，按 pod/app 元数据过滤 |
| `tencent-100050722703-cn-staging` 的配置 | 独立 fluent-bit → 集群内自建 Elasticsearch；该配置没有共享 Lua business namespace gate | 日索引 `addx-cn-staging-YYYY.MM.DD`；检查 input 的 Exclude_Path |
| 配置为 Kafka output 的集群（如 TKE cn-main、AWS prod/tech） | fluent-bit → Kafka → 实际配置的 Vector / 下游消费者 → ES/OpenSearch | 从实际 topic 与 consumer mapping 查 canonical / shadow index |

腾讯云 staging 的 Git 配置存在，不代表 `staging-cn` 或 `staging-cn-tke`
已经获准改路由：环境解析仍遵循 `data/env-keywords.yaml` 和集群准入门禁。
AWS 与腾讯云 CN staging 的 frontend 清单都使用 `logs-cn-staging.addx.live`；
只凭域名不能判定当前 DNS/CLB 的后端或集群生命周期，验收时必须核对。

## 应用侧 label 合同

```yaml
spec:
  template:
    metadata:
      labels:
        app: <kebab-case-app-name>
        env: <env-keyword>  # 如 staging-us / prod-us-restricted-admin
```

`env` 用路由表中的完整 keyword。Rollout 是 CRD，统一用显式 patch 写入
`spec.template.metadata.labels`；不要假定 Kustomize 顶层 `labels:` 会处理
Rollout。StatefulSet / Deployment 是内置资源，并非 CRD；本 workflow 仍优先
使用显式 pod-template patch，避免同时改变 selector。已有标签只需验证渲染结果，
不强制重写合法的注入方式。

验收必须解析完整的 `kustomize build` YAML stream，检查对应 workload 的
`spec.template.metadata.labels`；不要用 `grep -A2 "kind: Rollout"` 查找远处的
嵌套 label。可运行 `validators/validate.sh` 并使用 `workflows/add-logging.md`
的结构化标签检查。

## 共享 Lua business namespace gate

仅在目标 collector 确实调用共享 `set_kafka_topic` Lua 时适用：

- `namespace_name == default`，或以 `staging-` / `pre-` / `prod-` / `canary-` /
  `test-` 开头才放行；其他 namespace 直接 drop。
- 判断早于 annotation / label；`logging.addx.io/kafka-topic` 不能绕过 gate。
- AWS 三个 staging 的 direct OpenSearch output 仍调用该 Lua，因此 gate 仍生效。
- 腾讯云独立 staging 的现有 fluent-bit 配置未调用该 Lua，不能套用同一 drop 结论。

非 business namespace 若确实被目标 Lua 过滤，ES 入库断言记
`NOT_APPLICABLE` / `BLOCKED: namespace filtered`，用 `kubectl logs` 验证 stdout。
这不意味着要把应用迁入 `default` 或新建 `pre-*`；新部署仍须遵守环境和 namespace
合同。不要为了单个 E2E namespace 修改平台全局白名单。

## Kafka topic 与直写 index 分开检查

Kafka output 链路中，先查 collector 的 `kafka_topic` 和下游消费规则：

- business namespace 内的 topic annotation 优先；缺 env 时共享 Lua 会产生
  `addx-uncategorized-*`。是否消费该 topic，要以当前下游配置为准。
- 当前共享 Lua 按最后一个连字符拆 `environment` / `region`，简单 keyword
  `staging-us` 产生 `addx-us-staging-<app>`。
- 完整 keyword 带额外后缀时不能按语义猜 topic。例如现有解析会把
  `prod-us-restricted-admin` 拆成 `environment=prod-us-restricted`、`region=admin`。
  保留应用 label 合同，记录平台解析与消费 mapping 的不一致，由平台 GitOps 修复；
  不要擅自把 label 改成另一环境 keyword，也不要宣称日志已入库。
- canonical / universal shadow index、消费范围与是否落库都由实际 Vector / 下游
  配置决定；只有配置确实启用了 `vector-universal-pilot-*` 才查该前缀。

直写 OpenSearch / ES 的链路按 output 的 `Index` 或 `Logstash_Prefix` 查找。
即使 Lua 写入了 `kafka_topic` 字段，也不表示存在 Kafka hop；缺 env label 是应用
合同缺失，但不能直接断言“未消费 topic 导致日志丢失”。US/CN staging 的共享日索引
和 EU staging 的 alias 不含 app 名，需用文档中的 Kubernetes/pod metadata 过滤。

## 显式 audit / E2E 的验收

1. 确认目标 pod 有 stdout/stderr，并记录 cluster、namespace、pod 和时间窗口。
2. 读取实际 collector input/filter/output，确认文件采集、namespace/filter gate 和
   标签；检查 fluent-bit DaemonSet Ready 与目标节点覆盖。
3. 根据链路分支检查：Kafka 链路查 broker/topic、实际 consumer、lag 和 bulk 错误；
   direct 链路查 ES/OpenSearch output 连接、鉴权、重试、写入/容量错误。
4. 在实际目标 index/alias 中查到该 app 的近期文档。共享 index 存在不等于该 app
   已入库；缺少不属于本链路的 Kafka / Vector 不构成 blocker。
5. 缺访问或平台证据时明确 `NOT_VERIFIED` / `BLOCKED` 和 Ops Todo；label 验证
   仅能证明 manifest 接入合同，不能替代端到端结果。

## 日志格式与查看入口

业务 stdout/stderr 推荐 JSON（例如 `level`、`ts`、`msg`、`trace_id`），但是否拍平
取决于 collector / downstream parser。当前 direct staging 配置保留 Kubernetes
metadata，`Merge_Log Off`；不能保证任意 JSON 自动成为顶层可检索字段。

浏览器入口查目标集群 `log-frontend` 或既有日志平台配置。AWS US/EU staging
Git 配置分别为 `https://logs-us-staging.addx.live`、
`https://logs-eu-staging.addx.live`，使用 Casdoor 和 read-only logviewer。
不要给全 fleet 返回一个固定 Kibana URL，也不要承诺全员有建 index pattern
权限；权限以目标平台现行合同为准。缺 index pattern 交平台 GitOps / 受支持 API
流程，不能直接改 Elasticsearch `.kibana` 系统索引。

## 来源与关联

本参考的 collector/topology 证据固定于
[DEV/k8s 1882119f](https://gitlab.addx.ai/DEV/k8s/-/tree/1882119f9bd620b70342ac82b3e7126276e402b7)：

- `cicd/apps/fluent-bit/values.yaml`：共享 Lua gate 和 topic 解析。
- `clusters/aws-390709477306-{us,eu}-staging/fluent-bit-universal/values-override-*.yaml`、
  `clusters/aws-801447536674-cn-staging/fluent-bit-universal/values-override-*.yaml`：直写 output。
- `clusters/tencent-100050722703-cn-staging/logging/fluent-bit.yaml`、
  `log-frontend/values-override.yaml`：独立 TKE staging 采集与入口。
- `clusters/tencent-100014919455-cn-main/fluent-bit-universal/values-override.yaml`：Kafka output。
- `docs/runbooks/staging-logging-opensearch.md`：AWS staging 运维路径；其中历史 live 日期
  不能作为本次线上验收结果，控制面状态必须重新读取。

接入用 `workflows/add-logging.md`；故障诊断用
`troubleshooting/log-pipeline-silent-loss.md`；环境和 namespace 用
`references/data/env-keywords.yaml`。本参考不授权平台配置修改。
