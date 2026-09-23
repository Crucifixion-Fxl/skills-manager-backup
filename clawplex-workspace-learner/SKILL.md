---
name: clawplex-workspace-learner
description: 从群聊对话历史中自动提取项目级知识（踩坑经验、架构决策、最佳实践、项目事实），写入按群共享的 Letta Source。高质量条目自动晋升为 skills-library 中的标准 Skill。由 Temporal Cron 每 60 分钟定时触发，在 Sandbox 新建 thread 中执行。当你收到包含「知识提取」「knowledge extract」的 prompt，或被要求从对话历史中提取和归档知识时，使用此 Skill。
---

# ClawPlex Workspace Learner

## Description

从群聊对话中提取项目级知识，写入按群共享 Source，高分条目晋升为 skills-library Skill。

你运行在一个 Sandbox thread 中，由 Temporal Cron 定时触发。你的 prompt 会包含以下参数：

- `chat_id` — 群聊 ID
- `cursor` — 上次提取的消息 ID（为空则全量提取）
- `source_id` — 目标 Letta Source ID（`knowledge-group-{chat_id}`）

### 提取流程

1. **读取对话历史** — 使用 `archival_memory_search` 或 Letta SDK 读取 cursor 之后的消息
2. **按 chunk 处理** — 每 ~10K 字符为一个 chunk，逐 chunk 提取
3. **提取知识条目** — 按下方分类规则，输出 JSON 格式
4. **写入 Source** — 使用 `sources.passages.create()` 将每条知识写入目标 Source

---

## Rules

### 分类与评分

| 分类 | 含义 | importance 范围 |
| --- | --- | --- |
| **LESSON** | 踩坑经验、排障记录、失败教训 | 0.85 - 0.95 |
| **DECISION** | 技术选型、方案权衡、架构决策 | 0.80 - 0.90 |
| **PATTERN** | 最佳实践、可复用模式、工作流 | 0.75 - 0.85 |
| **FACT** | 项目事实、版本号、配置参数 | 0.70 - 0.80 |

### 提取原则

1. **原子化** — 每条 <= 480 字符，一条对应一个独立知识点
2. **技术层面** — 只提取架构、运维、配置、排障、工具使用相关内容
3. **不贴代码** — 使用自然语言描述。可执行的代码 artifact 属于 Tier 2 skills-library
4. **去重** — 如果 prompt 中附带了已有知识摘要，跳过重复内容

### 安全红线

**绝对禁止提取以下内容：**

- 密码、token、API key、secret
- PII（姓名、身份证号、手机号、邮箱与个人对应关系）
- 组织架构、人事任命、薪资信息
- 商业策略、财务数据、合同细节
- 客户信息、用户数据
- 内部 IP 地址、内部域名

遇到以上内容**必须跳过**，不提取、不记录、不引用。

### 输出格式

严格输出以下 JSON，不要附加任何其他文字：

```json
{
  "entries": [
    {
      "category": "lesson",
      "importance": 0.85,
      "text": "知识条目内容"
    }
  ]
}
```

### Tier 2 — 知识晋升为 Skill

对高分条目执行晋升评估：

- `importance` >= 0.85
- `category` 为 LESSON 或 PATTERN
- 可泛化：非当前项目特定、其他团队也能受益
- 纯技术内容，不含业务/财务/PII

晋升流程：筛选 -> 搜索已有 Skill -> 使用 skill-creator 变更 Skill -> 提交 MR（草稿，需人工审查）

---

## Examples

### Bad Example

```json
// BAD: 包含代码片段
{"category": "pattern", "importance": 0.80, "text": "使用以下代码连接数据库：conn = psycopg2.connect(host='db.internal', password='abc123')"}
```

```json
// BAD: 包含 PII
{"category": "fact", "importance": 0.75, "text": "张三的手机号是 138xxxx，负责 SRE 值班"}
```

```json
// BAD: 太长（>480 字符），混合了多个知识点
{"category": "lesson", "importance": 0.90, "text": "我们在部署时遇到了很多问题，首先是镜像拉不下来，然后发现是 DNS 的问题，后来又发现 K8s 的 node 上磁盘满了...（500+ 字符）"}
```

```json
// BAD: 不够技术，属于项目管理
{"category": "decision", "importance": 0.85, "text": "团队决定下个季度将人力从 A 项目转移到 B 项目"}
```

### Good Example

```json
// GOOD: 原子化的踩坑经验
{"category": "lesson", "importance": 0.90, "text": "Docker Hub 国内拉取失败时，正确做法是配置 registry-mirrors 镜像加速，而不是跳过依赖服务的测试。"}
```

```json
// GOOD: 清晰的架构决策
{"category": "decision", "importance": 0.85, "text": "知识提取触发从 ConversationLane idle 检测改为 Temporal Cron Schedule 每 60 分钟。原因：idle 在高频对话中可能长时间不触发；Cron 不依赖 Gateway 内存状态。"}
```

```json
// GOOD: 可复用的模式
{"category": "pattern", "importance": 0.80, "text": "ClawPlex 中所有 Skill 变更（创建或更新）统一使用 skill-creator，确保 frontmatter、必需章节、Bad/Good Examples 格式一致。"}
```

## API Reference

提取流程中用到的核心 API，参考 `references/extraction_format.md`.
