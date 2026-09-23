# Step 2.5: Baseline 捕获

变更前必须捕获基线指标到 `${A4X_RESOURCE_CHANGES_DIR:-$HOME/Project/A4x/resource_changes}/<YYYY-MM-DD>/<change-slug>/baseline/`。没有 baseline 就没法对比是否误伤现有 topic/消费者。

## 通用前置：起 Prometheus port-forward

```bash
# 查集群里的 prometheus service 名（可能叫 prometheus-server 或 thanos-query）
kubectl --context <ctx> -n prometheus get svc | grep -iE "prometheus|thanos"

# port-forward 起一个
kubectl --context <ctx> -n prometheus port-forward svc/prometheus-server 19090:80 > /tmp/pf.log 2>&1 &
disown
sleep 4
lsof -iTCP:19090 -sTCP:LISTEN | head -1  # 确认起来了
```

port-forward **会间歇掉线**，下面每组查询前最好先用 `curl http://127.0.0.1:19090/-/ready` 探活，掉了就重起。

## 定位 kafka 指标 job

不同地区的 log kafka 在不同 job 下暴露。先看已知表，缺的再自己扒：

### 已知 job 速查

| 地区 | log kafka Prometheus job | 备注 |
|---|---|---|
| EU | `eu-prod-public-kafka-metrics` | 3 exporter 端口 19309/29309/39309 对应 1a/1b/1c 独立集群 |
| US | _未经本 skill 验证_，使用下面的候选扫描命令自查 | — |
| CN | _未经本 skill 验证_ | CKafka，exporter 可能不在同一 Prometheus 下 |

### 候选扫描命令

```bash
curl -sSG "http://127.0.0.1:19090/api/v1/query" \
  --data-urlencode 'query=up{job=~".*kafka.*"}' | \
  python3 -c "
import sys,json
d=json.load(sys.stdin)
for r in d['data']['result']:
  print(r['metric'].get('job'), r['metric'].get('instance'), 'up=', r['value'][1])"
```

### 区分业务 Kafka 和日志 Kafka

- **业务 Kafka**（MSK/其他）: job 名通常是 `*-kafka-metrics`（无 `public`），topic 名不带 `addx-*`
- **日志 Kafka**（本 skill 的接入目标）: job 名通常带 `public`（如 `*-public-kafka-metrics`），topic 名符合 `addx-{region}-{env}-*` 模式

记下正确的 job 名，后面所有查询都要加 `{job="<log-kafka-job>"}` 筛选。**别把业务 Kafka 和日志 Kafka 搞混**——业务 Kafka 上也能看到很多高速 topic，但它们和日志接入无关。

---

## Baseline 必采 6 项

所有查询结果都保存到 JSON 文件，供变更后 diff。

### B1: FluentBit + Vector pod 健康

```bash
BASELINE=${A4X_RESOURCE_CHANGES_DIR:-$HOME/Project/A4x/resource_changes}/<date>/<slug>/baseline
mkdir -p $BASELINE

# FluentBit pods
kubectl --context <ctx> -n logging get pods -o wide > $BASELINE/fb-pods-before.txt

# Vector pods
kubectl --context <ctx> -n vector get pods -o wide > $BASELINE/vector-pods-before.txt

# 汇总
echo "FB  total: $(awk 'NR>1' $BASELINE/fb-pods-before.txt | wc -l)"
echo "FB  not-ready: $(awk 'NR>1 && $2!="1/1"' $BASELINE/fb-pods-before.txt | wc -l)"
echo "FB  with-restart: $(awk 'NR>1 && $4+0>0' $BASELINE/fb-pods-before.txt | wc -l)"
echo "VEC total: $(awk 'NR>1' $BASELINE/vector-pods-before.txt | wc -l)"
```

记下 "not-ready" / "with-restart" 的具体 pod 名，变更后同类状态的 pod **不能超过这个基数**。

### B2: 每个 topic 的 5m produce rate

```bash
curl -sSG "http://127.0.0.1:19090/api/v1/query" \
  --data-urlencode 'query=sum by (topic) (rate(kafka_topic_partition_current_offset{job="<log-kafka-job>"}[5m]))' \
  -o $BASELINE/topic-produce-rate-before.json

python3 -c "
import json
d = json.load(open('$BASELINE/topic-produce-rate-before.json'))
items = sorted([(r['metric']['topic'], float(r['value'][1])) for r in d['data']['result']], key=lambda x:-x[1])
print('=== TOP 15 produce rate ==='); [print(f'  {t:<55s} {v:>10.2f}') for t,v in items[:15]]
"
```

**记录热点 topic** 的绝对数字。变更后做对比时，不是看百分比，要看**哪些 topic 从几千 msg/s 跌到几十**。

### B3: 每个 topic 的总 offset 快照

```bash
curl -sSG "http://127.0.0.1:19090/api/v1/query" \
  --data-urlencode 'query=sum by (topic) (kafka_topic_partition_current_offset{job="<log-kafka-job>"})' \
  -o $BASELINE/topic-offsets-before.json
```

用于回查"变更过程中某 topic 有没有继续产生消息"。B2 是速率，B3 是累计值。

### B4: 所有 consumer group lag

```bash
curl -sSG "http://127.0.0.1:19090/api/v1/query" \
  --data-urlencode 'query=sum by (consumergroup, topic) (kafka_consumergroup_lag{job="<log-kafka-job>"})' \
  -o $BASELINE/consumer-lag-before.json

python3 -c "
import json
d = json.load(open('$BASELINE/consumer-lag-before.json'))
items = [(r['metric'].get('consumergroup','?'), r['metric'].get('topic','?'), float(r['value'][1])) for r in d['data']['result']]
print(f'total lag entries: {len(items)}')
print(f'total lag sum:     {sum(l for _,_,l in items):.0f}')
# 记下 Top 10 作为"正常高 lag"的参考
items.sort(key=lambda x:-x[2])
print('=== TOP 10 lag (baseline) ==='); [print(f'  cg={cg:<20s} {t:<55s} {l:>8.0f}') for cg,t,l in items[:10]]
"
```

### B5: 新 topic 先验检查（防重名）

准备接入的 topic 名在 kafka 里已经存在？

```bash
curl -sSG "http://127.0.0.1:19090/api/v1/query" \
  --data-urlencode 'query=kafka_topic_partitions{job="<log-kafka-job>",topic="<new-topic-name>"}' | \
  python3 -c "
import sys,json
d=json.load(sys.stdin)
if d['data']['result']:
  print('WARNING: topic already exists:')
  for r in d['data']['result']:
    print(f\"  instance={r['metric']['instance']} partitions={r['value'][1]}\")
else:
  print('OK: topic not yet created, will auto-create on first produce')
"
```

如果已存在但你并不知道谁创建的 → 追查，不要覆盖。

### B6: 节点资源 headroom（对新增 DaemonSet 必做）

```bash
kubectl --context <ctx> top nodes > $BASELINE/nodes-resource-before.txt 2>&1
```

如果上面报 `error: Metrics API not available`（metrics-server 未装或挂了），退一步用 `describe` 看节点 `Allocated resources`：

```bash
kubectl --context <ctx> describe nodes 2>&1 | \
  grep -E "^Name:|Allocated resources:|cpu\s+[0-9]+m|memory\s+[0-9]+Mi" \
  > $BASELINE/nodes-resource-before.txt
```

预估新 DS 每 pod 内存 req/limit × node 数 + 现有 Helm FB 共存（如有）是否会把某些节点推过压力水位。

---

## Baseline 对比的使用方式

变更后任一阶段门禁失败时，直接 diff：

```bash
# 例：检查所有 topic 的 5m rate 是否有 50% 下降
python3 -c "
import json
b = {r['metric']['topic']:float(r['value'][1]) for r in json.load(open('$BASELINE/topic-produce-rate-before.json'))['data']['result']}
import subprocess
now_json = subprocess.run(['curl','-sSG','http://127.0.0.1:19090/api/v1/query','--data-urlencode','query=sum by (topic) (rate(kafka_topic_partition_current_offset{job=\"<log-kafka-job>\"}[5m]))'], capture_output=True).stdout
n = {r['metric']['topic']:float(r['value'][1]) for r in json.loads(now_json)['data']['result']}
print('=== drops (was >1 msg/s, now <50%) ===')
drops = []
for t in sorted(set(b)|set(n)):
  bb,nn = b.get(t,0), n.get(t,0)
  if bb > 1 and nn < bb * 0.5:
    drops.append((t,bb,nn))
    print(f'  {t}: {bb:.1f} -> {nn:.1f}')
if not drops: print('  none')
"
```

**注意陷阱**：5m 窗口会被 rollout 空白期拉低。见 `verification-runbook.md` §2.1 "窗口拖尾"。
