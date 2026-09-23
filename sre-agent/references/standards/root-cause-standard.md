# 根因标准

Investigation Subagent 在 EVALUATE 阶段读取本文件，评估因果链是否完整。
Gather Sub-subagent 在编写 dimension_report 时可参考，了解什么是好的 finding。

---

## 1. 行业框架

| 框架 | 根因定义 | 核心判据 |
|------|---------|---------|
| 5 Whys（丰田） | 持续追问 "why" 直到触及可改变的系统/流程缺陷 | 可行动、可预防、非人为错误 |
| Google SRE | 因果链中最早的可干预点，干预后可阻止事故发生 | earliest actionable point |
| ITIL | 区分 immediate cause / contributing factor / root cause | root cause = underlying original cause |
| Fault Tree（NASA） | 用 AND/OR 门建模多条件组合，区分必要条件和充分条件 | 多根因可并存（AND 条件） |

---

## 2. 根因完整性 4 条判据

因果链完整 ⟺ 链首节点同时满足：

| # | 判据 | 定义 | 反例 |
|---|------|------|------|
| ① | 可行动 | 存在具体的、可执行的修复动作 | "恢复 thanos-cn"（目标，不是动作） |
| ② | 可解释 | 回答了"为什么发生"而不只是"发生了什么" | "thanos-cn 不可达"（状态，不是原因） |
| ③ | 最早点 | 再追问一层 why 得到的是设计决策或外部不可控因素 | "OOMKilled"（还能追问 why OOM） |
| ④ | 可预防 | 修复后可阻止同类事故重现 | "重启 Pod"（治标不治本） |

---

## 3. 因果链节点分类

Investigation Subagent 在 BUILD_CHAIN 阶段为每个节点标注类型：

| 类型 | 含义 | 需要修复方案？ | 飞书图标 |
|------|------|---------------|---------|
| `root_cause` | 最早的可行动干预点 | **必须** short_term | 🔴 |
| `contributing_factor` | 非触发原因但加重影响 | 应有方案 | 🟠 |
| `intermediate` | 因果链中间环节 | 不需要 | ↓ |
| `symptom` | 可观测的异常状态 | 不需要 | ↓ |
| `amplifier` | 放大影响的系统配置 | 应有方案 | 🟡 |
| `impact` | 最终业务影响 | 不需要 | ⚫ |

---

## 4. 递归终止条件

Investigation Subagent 在 EVALUATE → DEEP_DIVE 循环中使用：

- 链首满足 4 条判据 → 停止
- 达到最大调查轮次（3 轮）→ 停止，标记 `depth_limit_reached: true`
- 再追一层 why 得到系统架构设计决策或外部不可控因素 → 停止（归入 `solutions.long_term`）

---

## 5. 正例与反例

### 案例 1：Thanos CN 假告警风暴

**❌ 不完整**：
```
thanos-cn 不可达 → Grafana 查询超时 → executionErrorState=alerting → 35+ 假告警
```
- ① ❌ "恢复 thanos-cn"不是修复动作
- ② ❌ 只说了"不可达"，没说 WHY
- ③ ❌ "为什么不可达？"必须回答
- ④ ❌ 不知道原因无法预防

**✅ 完整**：
```
🔴 thanos-query 无 resource limits (BestEffort QoS)
🔴 Ingress http-rules host 不一致 (2024-07-22 引入)
  ↓ thanos-store 重启时 query 重试积压 → OOMKilled
  ↓ Pod 重启 + CertError 阻塞 CLB 同步 (25min)
  ↓ thanos-cn.addx.live 不可达
  🟡 executionErrorState=alerting 放大
  ⚫ 35+ 假告警风暴
```

### 案例 2：Kafka consumer lag

❌ `Kafka consumer lag > 60000 → 消费延迟`（同义反复）

✅ `🔴 consumer Pod 内存 256MB（应 1GB）→ Full GC 3次/分钟 → 消费速率降 → lag 60000+`

### 案例 3：5xx spike

❌ `上游服务返回 5xx → 我们也 5xx`（"上游"是谁？）

✅ 自有服务：`🔴 payment-service v2.3.1 DB 连接池泄漏 → 连接池耗尽 → 500`

✅ 外部依赖：`🔴 [external] Stripe 503 + 🟠 无 circuit breaker → 级联 500`

### 案例 4：磁盘满

❌ `磁盘 > 95% → 写入失败`（WHY 磁盘满？）

✅ `🔴 日志未配置 rotation → access.log 47GB (+2GB/天) → 分区 96% → crash`

### 案例 5：边界 — 追查到什么深度

```
Node 内存压力 → Pod 驱逐
  → why? HPA 扩了太多 Pod 到同一 Node
    → why? node selector 限制     ← 🔴 root cause (可行动: 改 affinity)
      → why? 只有一个 node group  ← long_term (设计决策，非 proximate root cause)
```
