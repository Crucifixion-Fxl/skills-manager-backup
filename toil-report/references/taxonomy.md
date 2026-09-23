# Toil 分类体系与归纳规则

> 本文件由归纳 Agent 读取，不要在 SKILL.md 的 Agent prompt 里重复内联。

## 分类体系

综合 ITIL 4 Service Management / Google SRE Workbook / Gartner I&O，共 18 类，5 个色系。**每条任务只能归一类，互斥**。

### 🔴 运行态（被动触发，核心 toil）

1. **事件响应** — 告警/PagerDuty 驱动的紧急 triage
2. **问题排查** — 非紧急的 bug/异常定位（ticket-driven，无 RCA 前）
3. **故障修复** — 已定位 RCA 之后的实际修复动作

### 🟡 变更（计划性，toil）

4. **服务部署** — 新 service 上线或 image/chart 版本发布
5. **配置变更** — 已有服务的参数/YAML/开关调整（不改 image tag）
6. **基础设施变更** — 声明式创建/修改/删除 IaaS 资源（Crossplane/Terraform/CRD/裸 cloud API）
7. **资源扩缩容** — NodePool / HPA / DB 规格等容量调整
8. **数据迁移** — DB schema 变更、数据同步、存储迁移

### 🟣 安全合规（toil）

9. **访问权限** — IAM/RBAC/SSO/账号授权
10. **凭据管理** — 密钥/Token/Vault KV 的新建、轮换、查询
11. **证书管理** — TLS/CA 签发、续期、配置
12. **安全审计** — 漏洞扫描、CVE 响应、合规检查

### 🔵 观测与经济（toil）

13. **监控告警** — Dashboard / Alert rule / Metric 采集的配置与调整
14. **健康巡检** — 主动发起的 proactive check（非告警驱动）
15. **成本治理** — 账单分析、资源标签、空闲清理、right-sizing

### ⚪ 工程建设（**非严格 toil**，有 enduring value，视觉上单独区分但仍纳入统计）

16. **方案设计** — spec / ADR / 技术选型 / plan 撰写 / 代码 review
17. **自动化开发** — 脚本 / skill / 内部工具的编写与迭代
18. **文档沉淀** — runbook / README / 踩坑记录的独立产出

## 归纳规则

- **summary**：综合 recap + 工具调用理解"做了什么"，不照抄原始消息，15-30 字中文；必须包含**动作 + 对象**（如"us-tech-service 升级 Karpenter NodePool 至 1.30"而不是"配置 NodePool"）
- **category**：按语义判断，不靠 keyword 匹配；每条任务只归一类；如果一次会话横跨多类，以**耗时最长的阶段**为准
- **trigger**：告警/用户请求/报错驱动 → `被动`；自主规划/定期巡检 → `主动`
- **automation**：全程无需判断可脚本化 → `full`；需少量确认 → `semi`；核心需人工决策 → `manual`
- **repeat_key**：同类重复性工作填统一标识（如 `cert-renewal`、`vault-kv-write`、`sessions-skill-test`）；非重复填 `null`

## 输出格式（每个任务恰好一行 JSON）

```json
{"id": N, "summary": "15-30字中文摘要", "category": "分类名", "trigger": "被动|主动", "automation": "full|semi|manual", "repeat_key": "标识符或null"}
```

**严格要求**：
- 输出只含 JSON 行，不要代码块标记、不要说明、不要空行以外的文本
- `category` 必须严格使用上表 18 个分类名之一，不得自造
