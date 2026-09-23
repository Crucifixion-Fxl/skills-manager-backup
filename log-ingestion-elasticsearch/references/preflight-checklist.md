# Step 0: 前置调研 (Preflight Checklist)

**强制 hard-stop**：以下 7 个问题必须逐条答到**确切的、已验证的值**才能进入 Step 1。任何一个问题答不上来 → STOP，向用户追问后才能继续。禁止"先做再说"、禁止猜。

---

## Q1: 目标服务部署在哪个 kubectl context？

不是"哪个地区"，不是"哪个 AWS 账户"，而是**kubectl context 的字符串名**。ArgoCD URL / 业务名 / 环境名都不能直接当答案。

### 验证

```bash
# 从用户给的线索推断 context 后，必须实际验证
kubectl --context <guess> -n <ns> get pods | grep <app>
```

**通过条件**：至少 1 个 pod `Running`。

### 常见陷阱

| 线索 | 易错推断 | 正确做法 |
|---|---|---|
| ArgoCD URL `argocd-eu-tech-service.addx.live` | 以为目标在 `eu-prod` | URL 子域名 = 集群名，是 `eu-tech-service` |
| 业务术语 "EU staging" | 以为任何 EU 集群都行 | 列出所有 EU 相关 context，逐个 `grep` |
| ArgoCD app 在集群 A 管理 | 以为 app 部署到集群 A | ArgoCD 可以跨集群部署，查 `spec.destination.server` |

### 列出候选 context

```bash
kubectl config get-contexts | awk '{print $2}' | grep -iE "eu|us|cn"
```

---

## Q2: 真实 container 名是什么？

**ArgoCD app 名、Deployment 名、pod 前缀、container 名 完全可以不同**。FluentBit 的 path glob 必须匹配**真实 container 名**（K8s container log 文件命名是 `<pod>_<ns>_<container>-<id>.log`）。

### 验证

```bash
# 取一个 pod
POD=$(kubectl --context <ctx> -n <ns> get pods -l <label> -o name | head -1)

# 查其所有 container 名
kubectl --context <ctx> -n <ns> get $POD -o jsonpath='{.spec.containers[*].name}'
```

### 常见陷阱

session 2026-04-13 验证过的真实案例：

| ArgoCD app 名 | 实际 Deployment | container 名 |
|---|---|---|
| `naturehood-api-staging-eu` | `naturehood` | `naturehood` |

**不要假设名字有延续性。每次都跑 `kubectl get pod -o jsonpath='{.spec.containers[*].name}'` 验证，不要靠脑补。**

Path glob 需要用 `{container}`（真实容器名），不是 `{app}`（业务名）。具体拼法见 `config-templates.md` 的 "Path glob 的 3 段结构"。

---

## Q3: log 文件 glob 什么时候唯一匹配该 container？

path glob 必须**只匹配目标 container** 的日志文件，不能误匹配同 namespace 其他 deployment。

### 验证（必须在真实 FB pod 或 busybox 挂载 hostPath 里做）

```bash
# 方法 1：用 busybox pod 挂 hostPath /var/log/containers
kubectl --context <ctx> -n <ns> run ls-test --rm -i --restart=Never \
  --image=busybox:1.36 \
  --overrides='{"spec":{"volumes":[{"name":"logs","hostPath":{"path":"/var/log/containers"}}],"containers":[{"name":"b","image":"busybox:1.36","command":["sh","-c","ls /logs/ | grep -i <app>"],"volumeMounts":[{"name":"logs","mountPath":"/logs","readOnly":true}]}]}}'
```

**通过条件**：你的 glob 匹配的文件数 == 目标 container 的 pod 数，且不包含其他服务。

### 常见陷阱

- glob `naturehood-api*staging*.log` → 0 match（container 叫 `naturehood` 不是 `naturehood-api`）
- glob `naturehood-*_staging-eu_*.log` → 同 ns 有 `naturehood-admin`/`naturehood-app`/`naturehood-hub`，误匹配
- 正解 glob：`naturehood-*_staging-eu_naturehood-*.log`，前半匹配 pod 前缀，后半匹配 container 名

---

## Q4: 目标集群有日志管道吗？

**"FluentBit pod 在跑"不等于"管道在工作"**。必须验证整条 FB→Kafka→Vector→ES 的链路存在。

### 验证

```bash
# 4a: FluentBit DS 存在
kubectl --context <ctx> -n logging get ds

# 4b: FB OUTPUT 是否真的是 kafka（而不是 stdout）。排除被注释掉的行
kubectl --context <ctx> -n logging get cm fluent-bit -o yaml | \
  sed '/^\s*#/d' | grep -iE "^\s*Name\s+(kafka|stdout)"

# 4c: Vector 存在且运行
kubectl --context <ctx> -n vector get deploy

# 4d: ES endpoint 可达（通过集群内 pod）
kubectl --context <ctx> -n vector run es-test --rm -i --restart=Never \
  --image=curlimages/curl:8.5.0 -- \
  curl -sS "<es_endpoint>/_cluster/health"
```

**通过条件**：4a/4b/4c/4d 全部存在，且 4b 里 kafka OUTPUT block 存在未被注释。

### 常见陷阱（空壳集群）

FluentBit pod 在跑不代表它干了活。tech-service 集群的例子：
- FB DS 27 个 pod `Running`
- ConfigMap 里唯一 OUTPUT 是 `Name stdout`
- 日志写到 FB 自己的 stdout，然后 kubelet 又把那个 stdout 写回 `/var/log/containers/fluent-bit-*.log`
- **全程没有任何日志离开过集群**

这种"空壳"状态如果直接按常规 Skill 流程改 ConfigMap，会走到错误的配置上。遇到时：
1. 停下
2. 决定是否在该集群建立完整管道（见下一问）

---

## Q5: 目标集群能到达 log kafka brokers 吗？

跨集群场景（tech-service 想复用 eu-prod 的 log kafka）必须先验证网络连通。

### 验证

```bash
kubectl --context <ctx> -n default run nettest --rm -i --restart=Never \
  --image=nicolaka/netshoot -- sh -c '
for host in <broker-1a> <broker-1b> <broker-1c>; do
  echo "== $host =="
  getent hosts $host
  timeout 3 nc -zv $host 9092
done'
```

**通过条件**：3 个 broker 的 DNS 解析 + TCP 9092 全通。

### 常见陷阱

- DNS 解析到的 IP 落在源集群 VPC 内网 → peering/TGW 没打通就废了
- 某一个 broker 通但另一个不通 → 可能有 security group rule 不一致，rollout 时分 AZ 灰度会暴露出来

---

## Q6: FluentBit 是谁管的？

决定采用哪种**部署/修改**模式，选错后续步骤全白做。

### 验证

```bash
# 6a: 是不是 Helm 装的？
helm --kube-context <ctx> -n logging list

# 6b: 是不是 ArgoCD 管的？
kubectl --context <ctx> -n argo-cd get applications -o json 2>&1 | \
  python3 -c "
import sys,json
d=json.load(sys.stdin)
for a in d['items']:
  spec = a['spec']
  srcs = spec.get('sources',[]) or [spec.get('source',{})]
  for s in srcs:
    p = s.get('path','')
    if 'fluent-bit' in p or 'vector' in p:
      print(a['metadata']['name'], '->', p)
"

# 6c: 是不是 git 仓库 + 手动 apply 的？
ls ~/Project/A4x/k8s/clusters/*/fluent-bit/ 2>/dev/null
```

### 管理模式 → 修改策略

| 6a Helm | 6b ArgoCD | 6c git | 模式 | 修改策略 |
|---|---|---|---|---|
| — | — | — | **全新集群**（没有任何日志管道） | 从 eu-prod 的 6 文件模板（3 CM + 3 DS）复制一份到新路径 `clusters/<new-cluster>/fluent-bit/`，走 "git + 手动 apply" 模式。注意补齐 SA/CR/CRB 或先用 `kubectl` 手动创建 |
| — | — | ✅ | **git + 手动 apply**（eu-prod 模式） | 改 git 仓库 ConfigMap，提 MR，人工 `kubectl apply` + rollout restart |
| ✅ | — | ✅ | **Helm + git zone DS 共存**（eu-prod 实际状态） | 不动 Helm，改 git zone DS/CM，复用 Helm 创建的 SA/CR/CRB |
| ✅ | — | ❌ | **仅 Helm 空壳** | 用 eu-prod 模式接管：新建 git zone DS/CM，复用 Helm SA，apply 后 `kubectl delete ds <helm-ds>` 删掉 Helm DS（保留 SA/CR/CRB/CM/Service 给新 DS 用）。**风险**：Terraform 如果重跑 `helm upgrade` 会重建 DS → 与新 zone DS 共存浪费资源，长期需协调 Terraform owner 从 IaC 移除 helm_release |
| — | ✅ | ✅ | **ArgoCD 管理** | 改 git，ArgoCD 自动 sync；**先看 Application 的 syncPolicy**（`automated.prune` 为 true 会自动删除），决定是否手动干预 |

---

## Q7: 目标应用日志是什么格式？

**为什么 hard-stop**：Vector transform 有 3 种范式（见 `config-templates.md` §"Vector transform 模板选择"），选错会导致**事件在 Vector 被静默丢弃或字段不展开**。必须在动配置前确定应用的日志行格式，再选对应模板。历史踩坑：apisix-gateway 套用了 naturehood-api 的 JSON 深解析模板，因 apisix 实际输出 nginx 纯文本，`parse_json!(.log)` strict mode 把所有事件丢弃（ES 收到 0 业务文档），Stage C G2 才抓到。

### 验证

```bash
kubectl --context <ctx> -n <ns> logs <sample-pod> --tail=30
```

对准前 20 行非空输出，判断格式：

| 观察到的每行格式 | 判定 | 用哪个模板 |
|---|---|---|
| 每行以 `{` 开头，`}` 结尾，是合法 JSON object | 结构化 JSON | B 或 C（看是否嵌套 `log.message`） |
| 每行是 nginx/apache 访问日志、lua/nginx error 日志、plain text | 纯文本 | **A（默认，最安全）** |
| 混合（有些行 JSON 有些行 text） | 按纯文本处理 | **A** |

**通过条件**：明确判定属于上表某一类，并在本轮调查笔记 / MR 描述中写下对应模板号（A/B/C）。

### 常见陷阱

- 只看 1-2 行就下判断 → 至少看 20 行，access log 和 error log 格式可能不同
- 看 `kubectl logs -f` 瞬时数据，只看到一种格式 → 拉 `--tail=100` 多样本
- 用 ArgoCD app 名相似度推断 → 技术栈一致不代表日志格式一致（naturehood-api 是 Go JSON，apisix-gateway 是 OpenResty/Lua 纯文本，都叫 `*-api`）

---

## Hard-stop 规则

任一问答不上或验证失败 → **立刻停下**，向用户明确汇报：
- 哪一个问题没过
- 查到的实际值是什么
- 需要用户提供什么额外信息

**不得跳过**、**不得假设值**、**不得"先写 MR 再说"**。本次错走 `eu-prod` vs `eu-tech-service` 就是跳过 Q1 的代价。
