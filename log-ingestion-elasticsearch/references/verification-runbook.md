# 验证 Runbook：健康门禁 + 端到端证据链 + 陷阱识别

本文档给 Step 7 (分阶段 apply + 健康门禁) 和 Step 8 (端到端证据链) 提供可执行的命令与判据。

---

## 1. 分阶段 apply 的 Gate 标准

### 1.1 灰度顺序（不得打乱）

```
Stage A: 1 个 AZ 的 FluentBit (CM + DS)
  ↓ G1-G4 全部通过 ↓
Stage B: 其他 2 个 AZ 的 FluentBit
  ↓ G1-G4 全部通过 ↓
Stage C: Vector ConfigMap + rollout restart 3 个 deployment
  ↓ G1-G4 全部通过 ↓
Stage D: **等待 30 分钟**，让 5m/15m 滑动窗口完整滚过 rollout 期间
  ↓
Stage E: 端到端证据链（§3）
```

Gate 是 **Stage 无关** 的——每个 Stage 都要跑 G1-G4 四项。任一 Gate 不通过 → **STOP + 回滚本阶段 + 报告**。禁止"先记下来继续做"。

Stage D 的 30 分钟观察期不能省——很多"持续下降"或"持续 lag 上涨"只有等 5m/15m 窗口完全离开 rollout 时段后才暴露。

### 1.2 Gate G1: Rollout 状态

对 FluentBit DS：
```bash
kubectl --context <ctx> -n <ns> rollout status ds <ds-name> --timeout=240s
```

对 Vector Deployment：
```bash
kubectl --context <ctx> -n vector rollout status deploy <deploy-name> --timeout=180s
```

**通过条件**：返回 `successfully rolled out`。超时或报错 → STOP。

### 1.3 Gate G2: Pod 健康

对 FluentBit pod（DaemonSet 的 pod label 如 `k8s-app=fluent-bit-logging-eu-central-1a`）：
```bash
kubectl --context <ctx> -n logging get pods -l <fb-selector> --no-headers | \
  awk '{print $3}' | sort | uniq -c
```

对 Vector pod（Deployment 的 pod label 通常是 `app.kubernetes.io/name=vector-a-zone` 或类似，先用 `kubectl get pods --show-labels` 确认）：
```bash
kubectl --context <ctx> -n vector get pods -l <vector-selector> --no-headers | \
  awk '{print $3}' | sort | uniq -c
```

**通过条件**：
- 全部 `Running`
- 零 `CrashLoopBackOff` / `Error` / `ImagePullBackOff`
- 新建 DS/Deploy 的 pod `restart count == 0`

同时逐个 pod 抓日志 grep error：
```bash
for p in $(kubectl --context <ctx> -n <ns> get pods -l <sel> -o name); do
  ERR=$(kubectl --context <ctx> -n <ns> logs $p --tail=200 2>&1 | grep -cE "error|warn|fail|unknown")
  [ "$ERR" -gt 0 ] && echo "$p errors=$ERR"
done
```

**通过条件**：0 pod 有 error/warn。如果有，去 §2 判断是否属于"已知假阳"白名单。

**Stage C（Vector apply）专查**：Vector rollout 后必须额外 grep transform runtime error，这是 transform 模板选错（见 config-templates.md "Vector transform 模板选择"）的首个可观测信号：

```bash
# 检查新 transform 是否在运行时 panic（strict parse_json! 在非 JSON 上会 drop 事件）
for p in $(kubectl --context <vec-ctx> -n vector get pods -o name); do
  kubectl --context <vec-ctx> -n vector logs $p --tail=500 2>&1 | \
    grep -E "journal-shaping-addx-.*-{app}.*parse_json|journal-shaping-.*{app}.*Mapping failed" | head -5
done
```

**通过条件**：新接入 `{app}` 的 transform 输出为空（无 `parse_json` runtime error，无 `Mapping failed with event`）。如果有，**回退到模板 A**（见 config-templates.md），事件正在被静默丢弃。踩坑记录：apisix-gateway 首轮用了模板 B 范式，Stage C G2 抓到 `parse_json ... trailing characters at line 1 column 5` → 定位为日志是 nginx 纯文本，改回模板 A 修复。

### 1.5 Gate G3: 现有 topic 5m rate 不下降

重新查询 B2（见 baseline-queries.md），diff baseline：

```bash
python3 -c "
import json, subprocess, sys
b = json.load(open('$BASELINE/topic-produce-rate-before.json'))
b = {r['metric']['topic']:float(r['value'][1]) for r in b['data']['result']}
now = subprocess.run(['curl','-sSG','http://127.0.0.1:19090/api/v1/query','--data-urlencode','query=sum by (topic) (rate(kafka_topic_partition_current_offset{job=\"<log-kafka-job>\"}[5m]))'], capture_output=True).stdout
n = {r['metric']['topic']:float(r['value'][1]) for r in json.loads(now)['data']['result']}
drops = [(t, b.get(t,0), n.get(t,0)) for t in sorted(set(b)|set(n)) if b.get(t,0) > 1 and n.get(t,0) < b.get(t,0) * 0.5]
if drops:
  print('DROPS:')
  for t,bb,nn in drops: print(f'  {t}: {bb:.1f} -> {nn:.1f}')
  sys.exit(1)
print('OK: no topic drops')
"
```

**通过条件**：无 drop，或所有 drop 已被 §2.1 "窗口拖尾"规则排除。

### 1.6 Gate G4: 消费 lag 不失控

```bash
python3 -c "
import json, subprocess, sys
b = json.load(open('$BASELINE/consumer-lag-before.json'))
b = {(r['metric'].get('consumergroup','?'),r['metric'].get('topic','?')):float(r['value'][1]) for r in b['data']['result']}
now = subprocess.run(['curl','-sSG','http://127.0.0.1:19090/api/v1/query','--data-urlencode','query=sum by (consumergroup,topic) (kafka_consumergroup_lag{job=\"<log-kafka-job>\"})'], capture_output=True).stdout
n = {(r['metric'].get('consumergroup','?'),r['metric'].get('topic','?')):float(r['value'][1]) for r in json.loads(now)['data']['result']}
high = [(k, b.get(k,0), n.get(k,0)) for k in sorted(set(b)|set(n)) if n.get(k,0) > max(b.get(k,0)*3, 500)]
if high:
  print('HIGH LAG:')
  for k,bb,nn in high: print(f'  cg={k[0]} topic={k[1]}: {bb:.0f} -> {nn:.0f}')
  sys.exit(1)
print('OK: no lag spikes')
"
```

**通过条件**：无任何 (cg, topic) 满足 `n > max(3×baseline, 500)`，或触发但经 §2.2 "rebalance 瞬间"规则排除。

### 1.7 回滚操作

```bash
# ConfigMap 回滚
kubectl --context <ctx> apply -f ${A4X_RESOURCE_CHANGES_DIR:-$HOME/Project/A4x/resource_changes}/<date>/<slug>/backup/<cm-name>.yaml

# DaemonSet 回滚（如果是新建的，就 delete）
kubectl --context <ctx> -n <ns> delete ds <new-ds-name>

# 或如果是修改的 DS，apply backup
kubectl --context <ctx> apply -f ${A4X_RESOURCE_CHANGES_DIR:-$HOME/Project/A4x/resource_changes}/<date>/<slug>/backup/<ds-name>.yaml
```

回滚后再查一次 Gate G3 / G4，确认 baseline 恢复。

---

## 2. 陷阱：假阳性识别

本次接入过程中踩过的 3 类假阳，写成识别规则。Gate 报警时先套这些规则排除。

### 2.1 窗口拖尾（5m vs 1m）

**症状**：Gate G3 报告某 topic 5m rate 下降超 50%（例如 `pre-iot-service` 23 → 10）。

**原因**：`rate()[5m]` 是对过去 5 分钟求速率，如果 rollout 期间有 15-30 秒 FB pod 处于 Terminating/ContainerCreating 状态，那 15-30 秒内的 produce 缺口会把 5 分钟平均值拉低，直到**这 5 分钟窗口整体走过那个缺口**为止。整个拖尾**最长持续 5 分钟**（窗口完全滑出缺口之前）。

**识别**：换窗口 + 等待。

```bash
# 用 1m 窗口重新 query
curl -sSG "http://127.0.0.1:19090/api/v1/query" \
  --data-urlencode 'query=sum by (topic) (rate(kafka_topic_partition_current_offset{job="<job>"}[1m]))'
```

如果 1m rate 接近 baseline （>80%）→ 确认是窗口拖尾，放行。

如果 1m 也低 → 真下降，继续排查（可能是 FB 误删、FB 背压、kafka broker 问题）。

**也可以用 deriv 检查趋势**：
```promql
deriv(sum by (topic) (kafka_consumergroup_lag{...})[2m:])
```
负值 = 在追赶，正值 = 继续堆积。

### 2.2 Rebalance 瞬间

**症状**：Gate G4 报告某 topic consumer lag 突然上涨（iot-service 210 → 634）。

**原因**：Vector 滚动重启的那一刻，consumer group `logstash` 会做 rebalance，partition 重新分配。rebalance 期间新 pod 还没 commit offset，kafka exporter 看到的 `group offset` 和 `topic offset` 差距瞬间变大。Rebalance 结束后 1-2 分钟自动追齐。

**识别**：
- 1 秒后重新 query，lag 数字显著下降 → rebalance 假阳
- 看绝对数：634 msg 对于 180 msg/s 的 topic = 3.5 秒缓冲，实际量级微不足道
- `deriv(lag)[2m:]` 为负 → 正在追赶

**真 lag 问题的信号**：
- lag 持续递增超过 5 分钟
- 绝对值 > `producer_rate * 300s`（即 > 5 分钟数据堆积量）
- `deriv(lag)[2m:]` 持续为正

### 2.3 Pre-existing error 混淆新引入

**症状**：新 Vector pod 的日志里有 `Mapping failed with event` error。第一反应："我的变更引入了 transform bug"。

**识别**：找**未经 rollout 的老 pod**的历史日志，看同一错误是否已经存在。

```bash
# 找还在跑的旧 pod（比如 Stage A 只 rollout 了 a-zone，b-zone 还是旧的）
# 先确认 b-zone 的 pod label 具体是什么，不要猜
kubectl --context <ctx> -n vector get pods --show-labels | grep vector-b-zone
# 用 pod name 子串匹配（避免 label 猜错）
OLD_POD=$(kubectl --context <ctx> -n vector get pods -o name | grep vector-b-zone | head -1)
kubectl --context <ctx> -n vector logs $OLD_POD --tail=500 | grep "Mapping failed"
```

如果旧 pod 也有同样的错误（且时间戳早于本次 rollout） → pre-existing，与变更无关。

同样，对 FluentBit 启动瞬间的 `UnknownTopicOrPartition`（4 次左右，秒级 burst）也是正常——librdkafka 初始化时对不存在 topic 的 metadata 请求会报一次，之后自动恢复。判据：
- 出现次数 < 10 且全部在启动后 10 秒内 → 正常
- `has been suppressed N times` 计数器持续增长 → 真 bug

### 2.4 VRL compilation warning（Vector transform）

**症状**：`WARN transform{...} VRL compilation warning. warnings= warning[E900]: unused variable 'err'`

**原因**：Vector 的 `., err = parse_json(.message)` 语法里，`err` 被声明但没用，Vector 0.32+ 开始会 warn。这是 code style warning，**不影响功能**。如果现有的 20+ transform 都有这个 warning，那么你新加的一条也会有。不是 bug。

**识别**：看 warning 内容是 `unused variable 'err'` 而不是 `type mismatch`、`undefined function` 等。

---

## 3. Step 8: 端到端证据链（10 跳）

**原则**：每一跳都要独立查到证据，**任何一跳拿不到证据 = 验证失败**。不能用"上一跳成功所以下一跳肯定也成功"来省步骤。

### 跳 1: 应用容器真实存在

```bash
kubectl --context <target-ctx> -n <app-ns> get pods | grep <app>
```
**期望**：≥ 1 个 pod Running。

### 跳 2: 容器日志文件符合 FB glob

```bash
# 在 FB pod 所在节点通过 busybox 挂 hostPath 看文件
kubectl --context <target-ctx> -n <ns> run ls-check --rm -i --restart=Never \
  --image=busybox:1.36 \
  --overrides='{"spec":{"volumes":[{"name":"logs","hostPath":{"path":"/var/log/containers"}}],"containers":[{"name":"b","image":"busybox:1.36","command":["sh","-c","ls /logs/ | grep -E \"<your-glob-pattern-regex>\""],"volumeMounts":[{"name":"logs","mountPath":"/logs","readOnly":true}]}]}}'
```
**期望**：列出的文件数 == 应用 pod 数（在当前节点上）。

### 跳 3: FB pod 与 app pod 在同一节点

```bash
# 对每个 app pod
for p in $(kubectl --context <ctx> -n <ns> get pods -l <app-label> -o name); do
  NODE=$(kubectl --context <ctx> -n <ns> get $p -o jsonpath='{.spec.nodeName}')
  FB=$(kubectl --context <ctx> -n logging get pods --field-selector=spec.nodeName=$NODE -l <fb-label> -o name)
  echo "$p  node=$NODE  FB=$FB"
done
```
**期望**：每个 app pod 都能找到同节点的 FB pod。

### 跳 4: FB 注册了 kafka output 到正确 topic

```bash
FB=$(kubectl --context <ctx> -n logging get pods -l <sel> -o name | head -1)
kubectl --context <ctx> -n logging logs $FB | grep "output:kafka"
```
**期望**：`[output:kafka:kafka.N] brokers='<correct-broker>' topics='<expected-topic>'`。

### 跳 5: Kafka 实际收到消息（offset 增长）

```bash
curl -sSG "http://127.0.0.1:19090/api/v1/query" \
  --data-urlencode 'query=sum by (instance) (kafka_topic_partition_current_offset{job="<log-kafka-job>",topic="<expected-topic>"})'
```
**期望**：**至少有一个 kafka 集群**的 offset > 0 且**持续增长**（30 秒后再查一次）。

> 注意：如果应用 pod 只分布在部分 AZ，其他 AZ 的 kafka offset 为 0 是正常的。

### 跳 6: Vector 订阅了新 topic 且有 lag 数据点

```bash
curl -sSG "http://127.0.0.1:19090/api/v1/query" \
  --data-urlencode 'query=kafka_consumergroup_lag{job="<log-kafka-job>",consumergroup="logstash",topic="<expected-topic>"}'
```

> Vector 默认 consumer group name 是 `logstash`（来自 vector-config.yaml 里的 `group_id = "logstash"`）。如果你改过，用改过的那个。

**期望**：至少 1 个数据点存在（表示 cg 已连上 topic）。空结果 = Vector 未订阅或 consumergroup 名错。

### 跳 7: Vector 不落后（lag ≤ 可接受阈值）

还是跳 6 的 query。
**期望**：`lag ≤ max(baseline*3, 500)` 且 **不在持续增长**。

### 跳 8: ES 索引存在且 green

（通过集群内 pod 访问 ES endpoint）
```bash
kubectl --context <vector-ctx> -n vector run es-check --rm -i --restart=Never \
  --image=curlimages/curl:8.5.0 -- \
  curl -sS "<es-endpoint>/_cat/indices/<expected-index-pattern>*?v"
```
**期望**：
- 索引存在（名字格式如 `addx-eu-staging-<app>-YYYY.MM.DD`）
- health = `green`
- `store.size` > 0

### 跳 9: ES 有文档

```bash
curl -sS "<es-endpoint>/<expected-index>*/_count"
```
**期望**：`count > 0` 且 30 秒后再查应该**更大**（持续写入）。

### 跳 10: 样本文档字段展开正确（transform 实际有效）

```bash
curl -sS "<es-endpoint>/<expected-index>*/_search?size=3&pretty"
```

**期望按 transform 模板分别判定**（见 config-templates.md "Vector transform 模板选择"）：

| 用了模板 | `_source` 必须有 | `_source` 不应有（证明 transform 生效，非原始 kafka 透传） |
|---|---|---|
| **A（纯文本）** | `@timestamp`, `stream`, `logtag`, `log`（`log` 是原始文本字符串） | `topic`, `offset`, `partition`, `source_type`, `message`（stringified） |
| **B（单层 JSON）** | 应用业务字段平铺在顶层（如 `level`, `caller`, `content`, `trace`, `span`） | `log`（应已被 merge 到顶层并 del）、`message`（stringified） |
| **C（嵌套 JSON）** | 同 B + 更深的内嵌字段平铺 | 同 B |

**不管哪个模板**：
- `@timestamp` 存在且为最近时间
- 没有 `_grokparsefailure` 之类的 tag

**失败模式速判**：
- `_source` 里只有 `{topic, offset, partition, source_type, message, timestamp, headers}` 这些 kafka 源字段 → transform **没生效**（常见原因：sink `inputs` 指向 source 而非 transform；或 transform 条件不匹配 topic）
- `_source` 里没有业务字段、`message` 是 stringified 的 FB JSON → transform **abort 了所有消息**（常见原因：strict `parse_json!` 在非 JSON `.log` 上 panic，模板选错了——应回落到 A）
- 分模板预期和实际 shape 不匹配 → 先翻 config-templates.md 的"已有服务 → 模板索引"确认你用的是哪个模板，再查 Vector logs

---

## 4. 决策速查：什么叫"变更成功完成"

打完勾表才能声明完成：

- [ ] G1-G4 对每个 rollout 阶段都通过
- [ ] 跳 1-10 全部有证据
- [ ] 已知的 Pre-existing error 和假阳（§2）已识别并放行，不是"忽略不看"
- [ ] 没有任何 topic 的 produce rate 在变更后 30 分钟内持续 < 50% baseline
- [ ] 没有任何 consumer group 的 lag 在变更后 30 分钟内持续 > max(3×baseline, 500)
- [ ] `${A4X_RESOURCE_CHANGES_DIR:-$HOME/Project/A4x/resource_changes}/<date>/<slug>/SUMMARY.md` 已写完（模板见 §5）
- [ ] backup/ 里的所有原始 yaml 都在

缺任何一项都不是"完成"。

---

## 5. SUMMARY.md 模板

`${A4X_RESOURCE_CHANGES_DIR:-$HOME/Project/A4x/resource_changes}/<YYYY-MM-DD>/<change-slug>/SUMMARY.md` 最小格式：

```markdown
# <变更简述> 日志接入

## 时间
<YYYY-MM-DD HH:MM>

## 操作人
<name> (via Claude Code)

## 变更范围
| 云 | 账户 | 集群 | 命名空间 | 资源 | 操作 |
|---|---|---|---|---|---|
| AWS | <acct> | <cluster> | logging | ConfigMap `<cm-name>` | apply |
| AWS | <acct> | <cluster> | logging | DaemonSet `<ds-name>` | rollout restart |
| AWS | <acct> | <cluster> | vector | ConfigMap `vector-config` | apply |
| AWS | <acct> | <cluster> | vector | Deployment `vector-{a,b,c}-zone` | rollout restart |

## 变更动机
<一段话：为什么做，背景是什么>

## 执行命令（按 Stage 分段）
```bash
# Stage A
kubectl ... apply ...
# Stage B
...
```

## 变更前后对比（抄 baseline 和 after 数字）
| 指标 | 变更前 | 变更后 |
|---|---|---|
| FB pod 总数 | <n> | <n> |
| 所有 staging topic 总 produce rate | <x> msg/s | <y> msg/s |
| logstash cg 总 lag | <a> | <b> |
| 新 topic `<topic>` offset | 0 | <growing> |
| 新 ES index `<index>` docs | — | <count> |

## 端到端证据
- [跳 1-10 的结果摘要 + 对应命令]
- ES 样本文档 1 条（展示 transform 是否正确）

## 备份
- `backup/<cm-name>.yaml` — <ConfigMap 完整 spec>
- `backup/fb-pods-before.txt` — 受影响 pod 快照
- `backup/topic-produce-rate-before.json` — topic rate baseline
- ...

## 回滚方式
```bash
kubectl --context <ctx> apply -f backup/<cm-name>.yaml
kubectl --context <ctx> -n <ns> rollout restart <kind>/<name>
```

## 已知风险与后续
- <列出任何 drift、残留配置、需要后续跟进的 Terraform 协调等>
```

**严禁**在 SUMMARY.md 里写密码/Token/Secret，需要引用凭据时用 `<CLIENT_SECRET>` 这种占位符，或指向用户明确批准的凭据来源。
