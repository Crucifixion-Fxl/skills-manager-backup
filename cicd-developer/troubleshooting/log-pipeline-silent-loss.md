---
name: log-pipeline-silent-loss
description: 应用有 stdout/stderr 但 ES/OpenSearch 查不到日志时，先确认目标 collector 的实际 filter 和 output，再按 direct 写入或 Kafka/consumer 链路排查。
---

# Playbook：日志缺失

## 症状与边界

应用在运行，但指定时间窗口查不到日志、索引断档或更新后日志不可见。
原因可能是应用未输出、collector 过滤、标签/查询条件错误，或实际下游故障。
**没有 Vector error 不代表全链路正常；目标本来直写 ES 时也不需要 Vector。**

先读 `references/logging/README.md`。只读诊断不得演变成平台修复：不直接改
collector、consumer、namespace、权限或 ES 系统索引；需要改应用标签时明确切换
到 `workflows/add-logging.md` 的获准 Build 范围。

## Step 1. 固定目标并验证源日志

记录 cluster/context、namespace、workload/pod、app、env keyword 和时间窗口。
对命令显式使用正确 context 和 namespace：

```bash
kubectl --context <context> -n <namespace> logs <pod> --tail=20
```

无输出时先排应用输出、container 选择、Pod 生命周期和日志轮转，不直接归因于
平台管道。有输出时保留一条不含敏感值的时间/字段匹配依据，继续查采集路径。

## Step 2. 读实际 collector 配置，选择分支

从目标 Argo Application source/path/valueFiles 找到 collector Git 配置；有权限时
再对照 live DaemonSet、ConfigMap 和 Ready 状态。记录 source revision，不能把 Git
声明当作 live 已生效。检查 input 的采集路径/Exclude_Path、filter 和 output：

- **direct**：output 是 OpenSearch/ES，按 `Index` 或 `Logstash_Prefix` 查索引/alias。
  AWS US/EU/CN staging 的当前 Git 配置走此分支，**无 Kafka / Vector hop**。
- **kafka-consumer**：output 是 Kafka，读取 broker/topic 和实际消费者/下游 mapping。
- 无法证明 topology：报告 `NOT_VERIFIED` 和缺少的权限/证据，不能猜测下游断链。

AWS staging 仍调用共享 Lua business namespace gate；腾讯云独立 staging 的
collector 配置未调用该 Lua。不能仅用“staging”或云厂商判断 filter 行为。

## Step 3. 先查实际 filter，再查 pod labels

```bash
kubectl --context <context> -n <namespace> get pod <pod> \
  -o jsonpath='{.metadata.namespace}{"\n"}{.metadata.labels}{"\n"}'
```

仅当目标确实调用共享 Lua 时，namespace `default` 或前缀 `staging-` / `pre-` /
`prod-` / `canary-` / `test-` 才放行。该判断在 annotation 和 label 之前；topic
annotation 不能绕过它。其他 collector 按自己的 input/filter 检查。

应用合同仍要求 `app: <canonical-app-name>` 和 `env: <env-keyword>`。缺标签要修，
但不能把所有缺标签都解释成传输丢失：direct output 可以继续写入共享索引，
Kafka output 则可能产生未被消费的 uncategorized topic。

| 发现 | 判定与后续 |
|---|---|
| 目标 filter 确实丢弃该 namespace/record | 记录精确 filter；CI/E2E 不适用则 `NOT_APPLICABLE`，业务日志要求则 `BLOCKED` + Ops Todo |
| 缺 app/env 或值不符 | 记录应用合同缺失；先检查是否只是查询字段不匹配，再按实际 output 查数据 |
| 标签正确但 topic 不符 | 对照实际 Lua 解析与 consumer mapping；不要凭 env keyword 的语义猜 topic |
| 标签/filter 正确 | 进入对应下游分支 |

不要为日志排障直接迁移 namespace、改 Argo destination 或把服务迁入 `default`。
这类修改需要独立的部署/迁移流程；新部署也不能使用已废弃的 pre 路由。

## Step 4. 验证渲染与标签来源

读取目标 overlay 的 Kustomization，运行 `kustomize build` 并结构化检查对应
workload 的 `spec.template.metadata.labels`。不要用 `grep -A2 "kind: Rollout"`
检查远处嵌套标签；可使用 `workflows/add-logging.md` 的完整 YAML stream 检查。

Rollout 为 CRD，显式 pod-template patch 是标准方式；StatefulSet / Deployment
是内置资源。已有合法 transformer/patch 渲染正确时，不因缺少 patches 字段而
重写它。修复标签也不能意外改变 selector。

## Step 5A. direct ES/OpenSearch 分支

1. 确认 fluent-bit DaemonSet 覆盖目标节点且 Ready，tail input 在采集目标容器。
2. 检查 output 连接、TLS/鉴权失败、重试、buffer、bulk 错误及写入/容量限制。
   不读取或打印 Secret 值；使用获准的只读状态和错误信息。
3. 按 output 的 index/alias 查询：US staging 为 `addx-us-staging-*`，EU staging
   为 `addx-eu-staging` alias，AWS CN staging 为 `addx-cn-staging-*`。
   这些是共享索引，需要匹配目标 pod/app/time；不能拼 `<app>` 到 index 名。
4. 检查 frontend 实际后端与用户查询权限。CN 的 AWS/TKE 清单出现同名入口时，
   不能仅凭 URL 判断查询的是哪个集群。
5. 没有 Kafka、Vector、consumer group 或 per-app index 不是此分支的 blocker。

## Step 5B. Kafka / consumer 分支

1. 检查 collector Kafka output 的 broker/topic 以及发送错误；再检查实际 topic
   是否有目标时间窗口的消息。记录证据，不输出敏感日志内容。
2. shared Lua 对 `staging-us` 产生 `addx-us-staging-<app>`；完整 env keyword 带
   后缀时必须读取真实解析结果。当前 Lua 对 `prod-us-restricted-admin` 会取
   最后一段 `admin` 为 region，属于平台解析/消费合同需核对的情况，不能通过
   擅改应用 env keyword 来掩盖。
3. 缺 env 可能生成 `addx-uncategorized-*`。确认实际消费者是否订阅、broker 保留
   和消息是否尚在，不能直接断言历史日志已永久丢失。
4. 检查实际 consumer 是否运行、group lag、mapping 以及 ES bulk 错误。
   canonical / `vector-universal-pilot-*` shadow index 仅在该下游配置启用时查找。
5. 未消费的数据是否可回补由 topic retention 和 consumer 重放能力决定；只读
   诊断不授权创建 consumer、重置 offset 或重放。源 Pod 日志仍在时可按权限查看。

## 结果与后续

- **PASS**：实际目标索引/alias 内查到目标 app 的近期文档，且 collector/filter 与
  查询链路匹配；只有标签正确不能判 PASS。
- **BLOCKED**：明确缺少已确认链路中的平台前置或权限，列出证据和 Ops Todo。
- **NOT_VERIFIED**：尚未证实 source 与 live 一致、topology 或应用文档未能核验。
- **NOT_APPLICABLE**：测试 namespace 明确被目标 filter 排除，且只验证 pod stdout。

日志格式建议以实际 parser 为准；不能假设 JSON stdout 一定自动拍平成顶层字段。
任何修复走对应 GitOps workflow；不直接改 Vector mapping、全局 namespace gate、
Elasticsearch `.kibana` 索引，或给应用增加另一套日志 sidecar 来规避平台合同。

## 参考

- `references/logging/README.md`：当前 Git topology、索引模型与固定版本来源
- `workflows/add-logging.md`：应用标签修复和结构化渲染检查
- `references/data/env-keywords.yaml`：环境、namespace 与新部署准入
