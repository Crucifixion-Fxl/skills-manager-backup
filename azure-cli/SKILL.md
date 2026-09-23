---
name: azure-cli
description: 通过 az CLI 管理 Azure 资源。当用户提到 Azure、Azure AI Foundry、Azure OpenAI、Azure AI Studio、VM、AKS、Storage Account、App Service、Azure SQL、Key Vault、Cognitive Services，或需要查询/操作 Azure 云资源时使用。即使用户只说"查一下 Azure 上的 OpenAI 资源"、"看看 Azure 的 VM"、"Azure 这个月花了多少"也应触发。
---

# azure-cli

通过 Azure CLI（az）协助运维和开发人员管理公司多账户的 Azure 资源。

## Description

本 Skill 提供 Azure CLI 的标准化操作流程，覆盖资源查询、状态检查和变更操作。Azure API 用法通过 WebSearch 查询官方文档，本 Skill 只记录公司特有的账户信息、命名约定和操作红线。

## 账户体系

公司采用多账户架构，每个账户对应独立的 Azure 订阅：

### 账户映射表

| 账户 | Subscription ID | Subscription Name | Tenant | 用途 |
|------|----------------|------------------|--------|------|
| `prod` | 5b72041d-c45d-421b-8563-e857e4f5017e | Azure subscription 1 | - | 生产环境 |
| `dev` | (待补充) | (待补充) | (待补充) | 开发环境 |

### 账户切换

Azure CLI 通过 `az login` 登录不同账户，每个账户有独立的认证 token：

```bash
# 查看当前登录账户
az account show

# 登出当前账户
az logout

# 登录其他账户
az login

# 查看所有可用订阅
az account list --output table
```

**重要**：不同于 AWS/GCP 的 profile 机制，Azure CLI 同一时间只能登录一个账户。切换账户需要先 `az logout` 再 `az login`。

### 资源组

按"用途领域"组织，RG 本身长期保留，资源可临时。新建资源前先判定归宿 RG，**不要为单个项目新建 RG** 除非确认不属于下表任一领域。**AI/OpenAI 资源池按环境拆 `prod-gpt` / `staging-gpt` 两个 RG**（其余领域不按 env / region / team 拆）。

| 资源组 | RG Location | 用途 | 装的典型资源 |
|--------|-------------|------|-------------|
| `prod-gpt` | eastus（资源跨区） | **生产侧** AI/OpenAI 资源池（prod / pre / crm / dev 等，**非 staging**） | `flywheel-llm-us`、`flywheel-llm-eu`、`algo-openai-prod-us-resource`、`algo-openai-crm-resource` 等 |
| `staging-gpt` | eastus（资源跨区） | **staging** AI/OpenAI 资源池（staging 各业务） | `flywheel-llm-staging-us`、`flywheel-llm-staging-eu` 等 |
| `internal-service` | eastus | 内部工具 / 服务集成 | `gitlab-codereview-neverexp`、`internal-openai-resource` |
| `saas-integration` | eastus2 | 第三方 SaaS 平台集成（CMS / Crowdin / 翻译等） | `addx-crowdin-translation` |
| `dev-research` | eastus2 | 各团队开发调研类临时资源（对外授权 / PoC / 探索） | `algo-openai-huaxiyun-clawcam-exp20260528` |

> **环境归属铁律**：prod 业务的 GPT 模型资源一律进 `prod-gpt`，staging 一律进 `staging-gpt`。
> **历史遗留（保留不删）**：`prod-gpt` 下原有的 `algo-openai-staging-us-resource` / `algo-openai-staging-eu-resource` 等 staging 资源**已弃用、不再用于新接入，但保留不删除**；新建 staging 资源一律走 `staging-gpt`。

### AI Services / Cognitive Services 资源命名规范

资源名 = **语义字段** + **生命周期后缀**，长度 ≤ 64 字符，全小写 + 短横线分隔。`az cognitiveservices account create` 时 `--custom-domain` 必须与 `-n` 取相同值，避免 endpoint 子域名与资源名脱节。

#### 铁律：只有临时资源带 lifecycle 后缀

```
有明确到期日 → 加 -exp{YYYYMMDD} 后缀    例 -exp20260528
无到期日(永久) → 不加任何 lifecycle 后缀（直接以 purpose 结尾）
```

**永久资源不带 lifecycle 后缀，临时资源必须带 `-exp{YYYYMMDD}`；不允许语义模糊后缀（如 `-resource`）**。名字本身揭示生命周期 —— 带 `-exp{date}` = 临时（到期巡检按日期清理），不带 = 永久。临时资源到期 → 直接 `az cognitiveservices account delete` + `purge` 整资源销毁。

> **`-neverexp` 后缀已废弃**：旧约定用 `-neverexp` 显式标永久，现改为"永久即无后缀"。已有带 `-neverexp` 的资源（`gitlab-codereview-neverexp` 等）保留不删、不强制改名，但**新建永久资源一律不加后缀**。

#### 总模板

```
{consumer-service}-[{source}-]{purpose}[-exp{YYYYMMDD}]
                                        └─ 仅临时资源带 ─┘
```

#### 字段规则

| 字段 | 含义 | 取值示例 | 必填 |
|---|---|---|---|
| `consumer-service` | **实际消费 Azure resource 的方** —— GitLab 仓库名 / 内部工具名 / SaaS 集成名 / 项目代号；语义"谁的代码会拿这把 key 调 API" | `cswebhook`（CUS/CSTools 部署形态）、`gitlab`（CI 集成）、`marketing-cms`、`crowdin`、`clawcam`（项目） | ✓ |
| `source` | 临时资源专用，标外部数据 / 平台来源 | `huaxiyun`（外部 SaaS 源）、`partner-x`（合作方） | 临时必填 |
| `purpose` | 模型在该服务里的真实用途 | `ticket-classification`、`translation`、`code-review`、`embedding-search`、`image-recognition` | ✓ |
| `lifecycle` | 生命周期标识，**仅临时资源带** | `-exp{YYYYMMDD}`（临时）；永久资源不带 | 临时必填，永久不带 |

> 历史 `tech` / `owner` 字段已废弃：tech 取值集仅 `openai` 一种区分价值低；owner（团队/组织）与 consumer-service 语义混淆，consumer-service 直接对应"代码 + 部署 + 运维责任人"链路更明确。未来真出现 AI 之外的 Cognitive 产品（Speech / Vision）再单独扩展。

#### 2 种命名模式（全员遵守）

| 模式 | 模板 | 适用 | 例（最佳实践） |
|---|---|---|---|
| **永久资源** | `{consumer-service}-{purpose}`（无后缀） | 长期持有的内部工具 / SaaS 集成 / 业务专用 | `cswebhook-ticket-classification` / `gitlab-codereview` / `flywheel-llm-us` |
| **临时资源** | `{consumer-service}-{source}-{purpose}-exp{YYYYMMDD}` | 一笔具体申请下发的临时资源（外部授权 / PoC / 调研） | `clawcam-huaxiyun-image-recognition-exp20260528` |

#### 历史命名（技术债，新建禁用）

下表是公司早期遗留命名，不符合现行规范但因迁移成本暂保留。**新建资源一律不准沿用这些写法**，未来迁移时按"应改写为"列重命名：

| 历史命名 | 不规范点 | 应改写为 |
|---|---|---|
| `algo-openai-{env}-{region}-resource`（prod / pre / staging / dev / crm 全系列） | `owner=algo` 含义不清（团队 ≠ 消费方）+ `-resource` 后缀语义模糊 | 按真实 consumer-service 拆分重命名（如 `<service>-<purpose>`） |
| `internal-openai-resource` / `litellm-resource` | `internal` 不是 consumer-service + `-resource` 后缀模糊 | `litellm` 已是 consumer-service → `litellm-llm-proxy` 之类；`internal-openai-resource` 按真实消费方拆分 |
| `addx-crowdin-translation` | `addx` 是组织前缀非 consumer-service | `crowdin-translation` |
| `admin-mir9o5sc-eastus2`、`admin-mir9nvcl-eastus2` | AI Foundry 自动生成名 | 不强制改（系统管理） |

#### 关键词速查

| 关键词 | 匹配资源 | RG | 命名状态 |
|--------|---------|---|---------|
| 生产 / prod + 美区 / US | `algo-openai-prod-us-resource` | prod-gpt | ⚠️ 历史命名 |
| 生产 / prod + 欧区 / EU | `algo-openai-prod-eu-resource` | prod-gpt | ⚠️ 历史命名 |
| 预发布 / pre + 美区 | `algo-openai-pre-us-resource` | prod-gpt | ⚠️ 历史命名 |
| 预发布 / pre + 欧区 | `algo-openai-pre-eu-resource` | prod-gpt | ⚠️ 历史命名 |
| staging + 新建（美/欧区） | 走规范命名建到 `staging-gpt`（如 `flywheel-llm-staging-us`） | **staging-gpt** | ✅ 新建一律走此 |
| staging + 美区（遗留） | `algo-openai-staging-us-resource` | prod-gpt | 🚫 已弃用不删，勿新接入 |
| staging + 欧区（遗留） | `algo-openai-staging-eu-resource` | prod-gpt | 🚫 已弃用不删，勿新接入 |
| 长期 dev | `algo-openai-dev-resource` | prod-gpt | ⚠️ 历史命名 |
| CRM | `algo-openai-crm-resource` | prod-gpt | ⚠️ 历史命名 |
| 内部 OpenAI | `internal-openai-resource` | internal-service | ⚠️ 历史命名 |
| GitLab Code Review | `gitlab-codereview-neverexp` | internal-service | ⚠️ 旧后缀（`-neverexp` 已废弃，保留不删；新建去后缀 = `gitlab-codereview`） |
| Crowdin / 翻译 | `addx-crowdin-translation` | saas-integration | ⚠️ 历史命名（`addx` 前缀，应改 `crowdin-translation`）|
| 临时调研 / 对外授权 | 走"临时资源"模板新建到 `dev-research` RG | dev-research | ✅ 规范命名（最佳实践，例 `algo-openai-huaxiyun-clawcam-exp20260528`）|
| AI Foundry 管理 | `admin-mir9o5sc-eastus2`、`admin-mir9nvcl-eastus2` | prod-gpt | 自动生成名，豁免 |

### 常用区域

| 区域 | 代码 |
|------|------|
| 美东 | eastus, eastus2 |
| 美西 | westus, westus2, westus3 |
| 美中 | centralus |
| 欧洲 | northeurope, westeurope |
| 英国 | uksouth, ukwest |
| 东南亚 | southeastasia |
| 东亚 | eastasia |
| 日本 | japaneast, japanwest |
| 澳大利亚 | australiaeast |

## az CLI 基础用法

```bash
az <service> <resource> <action> [--subscription <subscription-id>] [--resource-group <rg>] [--output <format>]
```

常用参数：
- `--subscription`：指定订阅 ID（可选，默认使用当前登录账户的默认订阅）
- `--resource-group` / `-g`：指定资源组
- `--output` / `-o`：输出格式（`table`、`json`、`yaml`、`tsv`）
- `--query`：JMESPath 查询过滤输出

## 常用服务速查

| 服务 | az 命令 | 说明 |
|------|---------|------|
| Virtual Machines | `az vm` | 虚拟机管理 |
| AKS | `az aks` | Kubernetes 集群 |
| Storage Account | `az storage account` | 存储账户 |
| Blob Storage | `az storage blob` | Blob 对象存储 |
| App Service | `az webapp` | Web 应用托管 |
| Azure SQL | `az sql` | SQL 数据库 |
| Azure AI Foundry | `az cognitiveservices` | AI 服务（OpenAI、AI Studio） |
| Azure OpenAI | `az cognitiveservices account` | OpenAI 服务管理 |
| Container Registry | `az acr` | 容器镜像仓库 |
| Key Vault | `az keyvault` | 密钥管理 |
| Virtual Network | `az network vnet` | 虚拟网络 |
| Load Balancer | `az network lb` | 负载均衡 |
| DNS | `az network dns` | DNS 管理 |
| Monitor | `az monitor` | 监控告警 |
| Log Analytics | `az monitor log-analytics` | 日志分析 |
| Resource Group | `az group` | 资源组管理 |
| IAM | `az role assignment` | 权限管理 |

## 执行流程

### Step 1: 执行命令

直接执行目标命令。如果报错（如 command not found、认证失败、配置缺失），读取 `references/setup.md` 并按引导帮助用户完成安装和配置。

### Step 2: 确认操作目标

根据用户描述确定：

1. **目标账户**：确认当前登录的是 prod 还是 dev 账户
   - 执行 `az account show` 查看当前账户
   - 如需切换账户，先 `az logout` 再 `az login`
2. **目标订阅**：如果账户下有多个订阅，用 `--subscription` 指定
3. **目标资源组**：Azure 资源按 Resource Group 组织，需确认 `-g` 参数
4. **操作内容**：判断读/写操作

### Step 3: 执行命令

```bash
az <command> [--subscription <id>] [-g <resource-group>]
```

## Rules

### 查询范围约束

**默认单账户查询**：除非用户明确要求（如"查看所有账户的资源"、"对比 prod 和 dev"），否则只在当前登录账户中查询资源，不要自动切换账户进行全局查找。

### 操作分级

#### 只读操作（直接执行）

所有 `list`、`show`、`get` 类命令均为只读：

- **VM**: `vm list`, `vm show`, `vm list-sizes`
- **AKS**: `aks list`, `aks show`, `aks get-credentials`（获取 kubeconfig）
- **Storage**: `storage account list`, `storage account show`, `storage blob list`
- **App Service**: `webapp list`, `webapp show`, `webapp log tail`
- **SQL**: `sql server list`, `sql db list`, `sql db show`
- **AI Foundry**: `cognitiveservices account list`, `cognitiveservices account show`, `cognitiveservices account keys list`
- **ACR**: `acr list`, `acr repository list`, `acr repository show-tags`
- **Key Vault**: `keyvault list`, `keyvault secret list`（注意：`keyvault secret show` 会暴露密钥值，需提醒用户）
- **Network**: `network vnet list`, `network lb list`, `network dns zone list`
- **Monitor**: `monitor metrics list`, `monitor log-analytics query`
- **Resource Group**: `group list`, `group show`
- **IAM**: `role assignment list`, `role definition list`

#### 高危操作（必须用户确认）

以下操作必须展示完整命令并获得确认：

- **创建资源**: `create`
- **修改资源**: `update`, `set`
- **删除资源**: `delete`
- **启停操作**: `vm start`, `vm stop`, `vm restart`, `vm deallocate`
- **AKS 变更**: `aks scale`, `aks upgrade`, `aks nodepool add/delete`
- **部署操作**: `webapp deployment`, `webapp config set`
- **DNS 变更**: `network dns record-set <type> add-record/remove-record`
- **Key Vault 写入**: `keyvault secret set`, `keyvault key create`
- **Storage 写操作**: `storage blob upload`, `storage blob delete`
- **IAM 变更**: `role assignment create/delete`

确认格式：

```
⚠️ 即将执行写操作：

  账户: prod (5b72041d-c45d-421b-8563-e857e4f5017e)
  资源组: my-rg
  命令: az vm stop --name my-vm -g my-rg

确认执行？(y/n)
```

### 操作红线

1. **禁止未确认的写操作** — 任何写操作都必须二次确认
2. **禁止暴露密钥** — Key Vault Secret、Storage Account Key、AI Service Key 等只展示前 4 位 + `***`
3. **禁止大范围删除** — 批量删除必须先列出受影响资源并确认
4. **Resource Group 删除需特别谨慎** — 删除 RG 会删除其中所有资源，必须列出所有资源并二次确认
5. **必须确认当前登录账户** — 执行操作前先 `az account show` 确认是 prod 还是 dev

### 输出优化

- 资源列表默认使用 `--output table` 提升可读性
- 需要程序化处理时使用 `--output json`
- 使用 `--query` (JMESPath) 过滤关键字段，避免输出过长
- 对于大量结果，结合 `--query` 和 `| head` 限制返回

## Examples

### Good

```
# 查询当前账户的 VM
用户：看看 Azure 上有哪些虚拟机

AI：先确认当前账户：
  [执行 az account show --query '{Name:name, SubscriptionId:id}' -o table]

  当前登录: prod (5b72041d-c45d-421b-8563-e857e4f5017e)

  查询 VM 列表：
  [执行 az vm list -o table --query '[].{Name:name, ResourceGroup:resourceGroup, Location:location, PowerState:powerState}']
```

```
# 写操作确认
用户：停掉那台测试 VM

AI：找到 VM：
  test-vm (resource-group: test-rg) - Running

  ⚠️ 即将执行写操作：

    账户: dev (待补充)
    资源组: test-rg
    命令: az vm stop --name test-vm -g test-rg

  确认执行？(y/n)
```

```
# 密钥保护
用户：查一下 Azure OpenAI 的 API Key

AI：查到 Cognitive Services Account: my-openai
  Endpoint: https://my-openai.openai.azure.com/
  Key1: sk-a1b2****（已脱敏，如需完整值请确认）
```

### Bad

```
# 未确认当前账户
用户：看看有哪些 VM
AI：[直接执行 az vm list] ← 应先确认当前登录的是 prod 还是 dev

# 未确认就执行写操作
用户：删掉这个 Storage Account
AI：已删除。 ← 未展示影响范围和确认

# 暴露密钥
AI：Key Vault Secret 值为 <LEAKED_SECRET>  ← 应脱敏
```

## 豁免

| 场景 | 条件 |
|------|------|
| 紧急故障恢复 | 用户明确声明紧急情况，可简化确认流程（仍需展示命令） |
| dev 账户 | 开发环境的非删除类写操作可简化确认 |
