# 安装与统一基线

## 单一事实源

- 固定版本：[`../assets/trufflehog-version.txt`](../assets/trufflehog-version.txt)
- 二进制来源：`trufflesecurity/trufflehog` 官方 GitHub Releases
- 文档来源：TruffleHog 官方文档（Running the scanner、Pre-commit hooks、Analyzers）

当团队批准升级版本时，只修改 `trufflehog-version.txt`。

## 统一规则

- 所有命令默认带 `--no-update`
- 只使用官方 release 资产，不使用不明镜像
- 下载后必须对照官方 checksum 文件做 SHA256 校验
- 优先使用仓库内置 checksum 文件；默认禁止远程回退 checksum（需显式开关）
- checksum 签名校验（cosign）为“可选但默认建议开启”
- 扫描产物默认写入系统临时目录
- Token 不得出现在 URL、命令行参数或报告中
- 报告中禁止输出 `Raw`，仅允许 `Redacted`
- 默认只看 `verified`；需要扩展排查时再启用 `unknown`
- 自建 GitHub/GitLab 的验证异常场景，按官方 on-prem verification 指南排查

## 安装方式

优先使用本 Skill 内置脚本，保证版本与校验规则一致：

- POSIX:
  `./skills/trufflehog-cli/scripts/install-trufflehog.sh`
- PowerShell:
  `.\skills\trufflehog-cli\scripts\install-trufflehog.ps1`

脚本行为：

- 从 `assets/trufflehog-version.txt` 读取版本
- 下载对应版本压缩包，并优先读取本地 checksum 文件
- 默认尝试使用 `cosign` 校验官方 checksum 签名（增强来源可信度）
- 校验通过后再解压安装

可选开关：

- `TRUFFLEHOG_VERIFY_CHECKSUM_SIGNATURE=0`：关闭 cosign 签名校验（默认开启）
- `TRUFFLEHOG_ALLOW_REMOTE_CHECKSUM=1`：允许无本地 checksum 时回退远程 checksum（默认关闭）

## 临时目录约定

每次运行使用独立临时目录：

- Linux / macOS:
  `${TMPDIR:-/tmp}/trufflehog-<workflow>-<target>`
- Windows PowerShell:
  `Join-Path $env:TEMP "trufflehog-<workflow>-<target>"`

## 最小报告字段

每次汇总至少包含：

- 扫描目标
- 流程类型
- TruffleHog 版本
- 结果模式
- 输出目录
- `verified` 数量
- `unknown` 数量（如启用）
- 覆盖边界（如 branch、commit range、max-depth）
