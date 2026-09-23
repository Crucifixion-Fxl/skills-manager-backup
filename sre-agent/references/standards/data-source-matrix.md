# 数据源选择矩阵

Investigation subagent 在 INVESTIGATE 阶段根据告警类型选择 gather 维度组合。

## 强制维度

所有告警组必须包含 `gather-preceding`（PD ±30min 告警 + ArgoCD/Jenkins 部署记录）。

## 选择矩阵

| 告警现象 | gather 维度组合（+ preceding） |
|---------|-------------------------------|
| Pod CrashLoop / OOM / Pending | k8s + prometheus + sentry |
| 5xx / 服务不可用 | prometheus + sentry + k8s + cloud |
| Latency 升高 | prometheus + cloud |
| 云资源异常（EC2/RDS/Redis/MSK） | cloud + prometheus |
| 主机级告警（CPU/内存/磁盘） | prometheus + cloud |
| Kafka 告警 | prometheus + cloud（判断是否维护） |
| 监控基础设施告警 | k8s + prometheus（查监控组件自身） |
| LLM/AI API 错误（Gemini/Bedrock/OpenAI） | cloud(GCP/AWS) + prometheus + k8s + sentry |

## 细粒度拆分参考

Investigation subagent 可根据 QUICK_ASSESS 结果将 dimension 拆为更细的子任务并行执行：

| Dimension | 细粒度拆分示例 |
|-----------|---------------|
| preceding | alerts（PD 告警）/ deploys（ArgoCD + Jenkins） |
| k8s | pods / nodes / ingress / services |
| prometheus | resources（CPU/内存/磁盘）/ traffic（QPS/错误率/延迟）/ infra（网络/连接池/队列） |
| sentry | per-project（CG 涉及多服务时） |
| cloud | 按云厂商拆（API 独立）；厂商内按资源类型拆（compute / database / network / messaging） |

拆分由 Investigation subagent 自行决定，不同子任务查询范围不得重叠。
