---
name: trufflehog-cli
description: 使用 TruffleHog CLI 进行本地密钥扫描、远程仓库扫描、pre-commit 接入，以及单凭证有效性校验。当用户提到 trufflehog、secret scan、泄露凭证排查、Git 历史扫描、远程仓库扫描、pre-commit、凭证轮转后验证、余额检查、用量排查、身份溯源或 key 归属时触发。
---

# trufflehog-cli

本仓库 TruffleHog CLI 的统一入口 Skill。

按需加载模块：

- 安装、版本锁定、校验和验证、临时文件策略：
  [install-and-baseline.md](references/install-and-baseline.md)
- 本地工作区 + 本地 Git 历史扫描：
  [local-scan.md](references/local-scan.md)
- pre-commit 接入：
  [pre-commit.md](references/pre-commit.md)
- 远程 GitLab 仓库扫描：
  [remote-repo-scan.md](references/remote-repo-scan.md)
- 单凭证识别与有效性验证：
  [credential-verify.md](references/credential-verify.md)
- LLM 模型 Key 余额与用量探针：
  [balance-probe.md](references/balance-probe.md)
- LLM 模型 Key 身份溯源探针：
  [identity-probe.md](references/identity-probe.md)
- 常见凭证类型与验证模式：
  [credential-types.md](references/credential-types.md)
- JSONL 报告字段说明：
  [trufflehog-jsonl-format.md](references/trufflehog-jsonl-format.md)

需要可复用、可审计的安装流程时，使用内置脚本：

- POSIX install:
  [install-trufflehog.sh](scripts/install-trufflehog.sh)
- PowerShell install:
  [install-trufflehog.ps1](scripts/install-trufflehog.ps1)

pre-commit 推荐使用内置包装脚本：

- POSIX pre-commit wrapper:
  [pre-commit-trufflehog.sh](scripts/pre-commit-trufflehog.sh)

## Description

将本 Skill 视为公司内 TruffleHog CLI 标准操作手册。

覆盖四类主流程：

- 本地扫描：开发机工作区与本地 Git 历史
- pre-commit 接入：在提交前阻断新泄露
- 远程仓库扫描：针对单个 HTTPS 远程仓库
- 凭证验证：确认泄露凭证是否仍可用

不要将本 Skill 扩展为通用 SAST、依赖漏洞扫描或代码审计。

## Rules

### Rule 1 - 先读统一基线

Read [install-and-baseline.md](references/install-and-baseline.md) first.

基线规则适用于所有流程：

- 版本以 [trufflehog-version.txt](assets/trufflehog-version.txt) 为单一事实源
- 必须使用官方 GitHub Release 二进制并校验官方 checksum
- 所有命令默认带 `--no-update`
- 扫描产物写入系统临时目录，不写仓库根目录
- Token 不得出现在仓库 URL 或命令行参数中
- 报告只允许使用 TruffleHog 的 `Redacted` 信息，禁止输出原文密钥

### Rule 2 - 一次只选一个主流程

先判断任务类型，再加载对应 reference：

- 本地仓库/开发机自查：
  [local-scan.md](references/local-scan.md)
- pre-commit 接入：
  [pre-commit.md](references/pre-commit.md)
- 远程 GitLab HTTPS 仓库扫描：
  [remote-repo-scan.md](references/remote-repo-scan.md)
- 单凭证泄露排查或轮转后验证：
  [credential-verify.md](references/credential-verify.md)
- LLM 凭证余额探针（凭证验证的可选后续步骤）：
  [balance-probe.md](references/balance-probe.md)
- LLM 凭证身份溯源探针（三步链路的第三步：有效性 → 余额 → 身份）：
  [identity-probe.md](references/identity-probe.md)

不要默认一次性加载全部 reference。

### Rule 3 - 命令与场景一一对应

按实际扫描范围选择命令族：

- `trufflehog filesystem .`：当前工作区文件
- `trufflehog git file://...`：本地仓库历史
- `trufflehog git <https-repo-url>`：远程仓库历史
- `trufflehog analyze` 仅在有人可交互操作 TUI 时使用

不要把同一条命令硬套到所有场景。

### Rule 4 - 最小权限优先

凭证权限按最小化原则：

- 仅做远程仓库克隆扫描时，优先 `read_repository`
- 仅当需要 GitLab API 级校验（如 PAT 自检）时才升级到 `read_api`/`api`
- 优先使用短时凭证，并在流程结束后显式清理

### Rule 5 - 报告必须明确范围与边界

每次结果汇总必须包含：

- 扫描对象
- 实际命令族
- 执行目录或目标仓库
- 结果文件位置
- `verified` 与 `unknown` 数量
- 范围限制（如 `--branch`、`--since-commit`、`--max-depth`）

未显式覆盖的分支，不得在结论中宣称已覆盖。

### Rule 6 - 强制扫描场景

以下场景必须执行 TruffleHog 扫描：

- **代码开源前**：必须对 Git 全量历史执行扫描，作为 [代码开源审批流](https://applink.feishu.cn/T95dQntGpJxG) 的前置条件
- **发现硬编码密钥时**：立即扫描 Git 历史确认影响范围，配合运维轮换密钥
- **密钥泄露事件响应**：扫描所有可能受影响的仓库，确认泄露边界
- **在技术博客、Stack Overflow 等公开渠道发布代码前**：等同于开源，须先扫描

密钥分级参考：L1（生产密钥）> L2（开发密钥）> L3（个人凭证）。发现 L1 密钥泄露须立即上报安全事件。

## Examples

### Bad

```text
用户说“扫一下仓库”，我直接跑一条通用命令，把 JSON 写到仓库根目录，
还输出了明文线索，最后宣称“所有分支都安全”。
```

问题：

- 命令与范围不匹配
- 污染工作区
- 泄露敏感信息
- 结论越权

### Good

```text
先按统一基线确认版本、安装和输出策略，再选择单一流程与对应命令；
产物写入临时目录，报告明确写清本轮覆盖与未覆盖边界。
```

优点：

- 单入口 + 单基线，避免重复维护
- 渐进加载，主文档保持简洁
- 规则集中，便于协作与审计
- 输出可追踪且不泄露敏感信息
