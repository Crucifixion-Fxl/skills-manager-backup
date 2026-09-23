---
name: security-compliance-review
description: Perform a structured company security and compliance review using evidence from code, configuration, and documentation. Use for MR/PR review, architecture review, and periodic compliance scans covering secrets, PII, employee access, and company red lines. Do not use as a comprehensive codebase vulnerability audit.
---

# Security & Compliance Review

综合性的安全与合规审查框架。基于输入材料与基线策略，智能判断风险面并给出可执行整改建议。

本 Skill 的 SSOT 是公司控制基线与合规红线。源码漏洞机理与完整覆盖审计由 [`codebase-vulnerability-analysis`](../codebase-vulnerability-analysis/SKILL.md) 负责；合规缺口不能因措辞相似自动升级为漏洞。

详细规则和示例参见 [references/REFERENCE.md](references/REFERENCE.md)。

## 执行流程

### Step 0: 前置门禁 - 系统类型判定（阻断）

- 必须先判断系统类型：`C 端系统` / `对内产品` / `混合`
- 若无法判定：先输出"澄清问题"（阻断结论），在拿到答案前不进入后续检查

### Step 1: 相关性智能判定

1. **先定 scope**：优先以 MR/diff/指定目录为准；缺省可扫描仓库
2. **再找证据**：代码/配置/CI/Helm/依赖
3. **再下结论**：`相关` / `不相关(N/A)` / `未知`，并区分 `compliance_gap` / `hardening` / `vulnerability_candidate`
4. `未知` 仅对"必须澄清项"发起澄清/阻断

### Step 2: 风险地图检查

必须输出表格：`风险类别 | 相关性 | 触发线索 | 处理方式 | 证据/建议`

必须覆盖的风险类别：
- `R0 系统类型`
- `R4 批量能力/导出`
- `R5 观测泄露（日志/埋点/错误上报）`
- `R6 Secrets/凭证`
- `R7 第三方边界`
- `R11 高敏数据零人工访问（视频/地址/电话等）`
- `R12 保留与删除/DSAR`
- `R14 Agent Skills 供应链`
- `R15 定位权限`

### Step 3: 按规则分类处理

#### 合规缺口、Hardening 与漏洞候选的边界

- `compliance_gap`：违反公司明确控制基线即可成立，按本 Skill 的规则处理；不要求先证明攻击链。
- `hardening`：防御纵深建议或通用最佳实践，但当前证据没有证明边界突破；只能作为建议，不能赋漏洞严重级别。
- `vulnerability_candidate`：必须同时写清较低信任主体、输入/动作、应有确定性控制、可达源码路径、跨越的信任边界、受影响主体/资源与具体后果。缺任一项不得称为漏洞。
- 代理、IdP、浏览器、云策略、部署 attachment、broker ACL 等决定性事实不在当前证据时，候选标 `needs_validation`，写清缺失事实和安全验证办法；不得猜存在或不存在，也不得赋漏洞严重级别。
- severity 只跟已证明影响走：不能把 crash 自动升为 RCE，把同一主体本来已有权执行的动作称为提权，或把单请求/单租户自耗称为共享可用性攻击。
- 命中需要源码漏洞机理分析的候选时，按 [`codebase-vulnerability-analysis`](../codebase-vulnerability-analysis/SKILL.md) 选择相关攻击域；本 Skill 仍拥有公司合规结论，漏洞状态以专项分析证据为准。

#### AI / Agent 的确定性控制边界

- guardrail prompt、tool description、模型自述和 JSON schema 不构成授权边界；有效边界必须是 handler 侧校验、resource-scoped authorization、隔离、不可变绑定和受限凭据。
- model output、memory、RAG 内容、tool metadata、MCP response 与 sub-agent 返回值均按不可信输入处理。
- approval 必须绑定规范化后的 tool 名、完整参数、请求人、目标资源、有效期以及适用的金额/批次/一次性状态；retry 或恢复会话后不得用旧批准执行变更或重复后的动作。
- sub-agent/MCP 只获得任务所需的最小上下文、凭据与工具；委派结果回流后仍需确定性验证，不能继承其“已批准/已校验”声明。

#### 公司规范项（默认强制，不澄清）

若证据不足：处理方式=`默认规范+建议`，并在缺口中标注"需补证据（非澄清）"：

- 统一 Observability SDK + 脱敏/导出限制/审计（R5）
- Secrets/凭证（R6）：
  - 密钥分级：L1（生产密钥，仅运维配置在 Vault）、L2（开发密钥，可用但不硬编码）、L3（个人凭证，仅限本地）
  - L1 密钥须通过 [密钥使用审批流](https://applink.feishu.cn/T95dQpjJsTtK) 审批，运维直接配置在 Vault 中，开发者禁止获取
  - L3 个人凭证禁止配置到任何线上环境（CI/CD、生产、Vault）
  - 密钥级别以实际能访问的最高环境为准，不确定时按 L1 处理
  - 禁止通过任何渠道（飞书/邮件/截图）传输密钥明文
  - 禁止在 Docker 镜像中打包密钥，Terraform state 禁止提交到代码仓库
  - 加密后的密钥也禁止提交到代码仓库（虚假安全感）
  - L1 密钥至少每 90 天轮换
- CI/CD 密钥管理：CI 阶段仅允许 L2 密钥（GitLab Variables, masked + protected），CD 阶段 L1 密钥只能通过 Vault + External Secrets 注入。禁止在 CI 中使用 L3 个人凭证
- 运维/支持访问边界：受限平台、默认脱敏、默认禁导出、工单绑定、全量审计

#### Agent Skills 供应链（R14，检测到 Skills 文件时触发）

若 MR/仓库中存在 `SKILL.md`、`.cursor/skills/`、`.cursor/rules/`、`AGENTS.md`：
- 检查 Prompt 注入模式（`ignore previous instructions`、`bypass safety` 等）
- 检查硬编码凭证（API Key、Token、连接字符串）
- 检查可执行脚本的真实能力与边界；存在 `scripts/` 本身不是违规，详细规则以 [`local-security-check`](../local-security-check/SKILL.md) 为准
- 检查 HTML 隐藏注释（`<!-- -->`，对人不可见但 LLM 可读）
- 检查内部信息泄露（内部域名、IP 地址）
- 若无上述文件：标记 `不相关(N/A)`

#### 必须澄清项（缺失则阻断）

- 系统类型
- 是否新增/放宽导出/批量能力（export/download/csv/xlsx/report/bulk/batch 线索）
- 第三方边界（新增/修改第三方 SDK/Webhook/外部 API、可见字段映射）
- 加密与密钥管理（涉及 PIN/password/OTP/token/key/Private Key 等）
- 新增 PII 字段与脱敏方案（user_id/SN/email 之外）

#### 定位权限（R15，公司级隐私红线）

**绝对禁止**在任何平台申请用户地理位置权限，包括但不限于：
- Android: `ACCESS_FINE_LOCATION`、`ACCESS_COARSE_LOCATION`、`ACCESS_BACKGROUND_LOCATION`
- iOS: `NSLocationWhenInUseUsageDescription`、`NSLocationAlwaysUsageDescription`
- Web: `navigator.geolocation`、Permissions API `geolocation`
- Flutter: `geolocator`、`location` 等定位插件

城市/地区信息**只能**通过用户主动选择获取，选择结果持久化存储，不重复询问。

#### 红线（发现则不通过）

- 员工（含运维/支持）可直接访问/导出原始视频：直接判定 `需整改/不通过`
- 明文存储/传输/日志记录密码、token、key 等凭证要素：高风险阻断
- 高敏数据（地址/电话）存在人工查看入口：直接判定 `需整改/不通过`
- 申请任何形式的地理位置权限：直接判定 `需整改/不通过`（R15）

## 输出结构

必须严格按以下结构输出 Markdown：

### 2.0 范围与前置（Scope & Gates）

```markdown
- **scope**: 本次 review 覆盖的模块/目录/PR 范围
- **系统类型与采用策略**: C 端系统 / 对内产品 / 混合
- **阻断项（如有）**: 列出阻断项（引用 R#/G#/E#）
- **澄清问题与未知项**: 仅覆盖必须澄清清单
```

### 2.1 总结结论（Summary）

```markdown
- **结论**: 通过 / 有条件通过 / 不通过
- **风险等级**: 低 / 中 / 高
- **风险项摘要**: 引用 R#
- **关键证据索引**: 只列 E#
```

### 2.2 风险地图（Risk Map）

| 风险ID | 风险类别 | 相关性 | 触发线索 | 处理方式 | 证据/建议 |
|--------|----------|--------|----------|----------|-----------|
| R0 | 系统类型 | 相关/N/A/未知 | 线索 | 澄清/默认规范+建议/阻断 | E#/建议 |

### 2.3 缺口清单（Gaps）

| Gap ID | 缺口描述 | 风险等级 | 关联风险项 | 证据引用 | 建议整改 |
|--------|----------|----------|------------|----------|----------|
| G1 | 描述 | 高/中/低 | R# | E# | A# |

### 2.4 建议与 Checklist（Actions）

| 优先级 | Action ID | 建议内容 | 关联缺口 | 证据引用 |
|--------|-----------|----------|----------|----------|
| 立刻修 | A1 | 具体建议 | G# | E# |

### 2.5 证据附录（Evidence）

| Evidence ID | 证据类型 | 引用 | 摘录 |
|-------------|----------|------|------|
| E1 | Doc/Code/Config | [path:Lx-Ly](path) | 摘录 |

## 示例

### ❌ Bad - 硬编码 Secret

```python
API_KEY = "sk-1234567890abcdef"  # 违反 R6
```

### ✅ Good - 使用 Vault

```python
API_KEY = vault.read('myapp/api-key')
```

更多示例参见 [references/REFERENCE.md](references/REFERENCE.md#完整示例)。

## 豁免

| 场景 | 条件 |
|------|------|
| 本地开发环境 | 仅用于本地测试的配置（不得提交到仓库） |
| 遗留系统迁移 | 正在进行合规改造的遗留系统（需提供迁移计划） |

豁免方式：`/override skill=security-compliance-review reason="..." evidence="..."`

## References

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [详细参考文档](references/REFERENCE.md)
- [源码漏洞分析专项](../codebase-vulnerability-analysis/SKILL.md)
- [Skill 包安全检查](../local-security-check/SKILL.md)
