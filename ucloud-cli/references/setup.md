# UCloud CLI 安装与配置

## 安装

### macOS (Homebrew)

```bash
brew install ucloud
```

### Linux (二进制安装)

```bash
# 下载最新版本（替换版本号）
curl -OL https://github.com/ucloud/ucloud-cli/releases/download/0.1.22/ucloud-cli-linux-0.1.22-amd64.tgz
tar zxf ucloud-cli-linux-0.1.22-amd64.tgz -C /usr/local/bin/
```

### 从源码编译

```bash
# 需要 git 和 golang
git clone https://github.com/ucloud/ucloud-cli.git
cd ucloud-cli
make install
```

### 升级

```bash
brew upgrade ucloud
```

## 认证配置

### 首次初始化（推荐）

```bash
ucloud init
```

交互式引导，按提示输入：
- PublicKey（从 UCloud 控制台 → API 密钥 获取）
- PrivateKey
- 默认 Region
- 默认 ProjectID

配置存储在 `~/.ucloud/` 目录下：
- `config.json` — Region / Zone / ProjectID / BaseURL 等
- `credential.json` — PublicKey / PrivateKey

### 添加 Profile

```bash
ucloud config add --profile <name> --public-key <key> --private-key <key>
```

### 查看已有 Profile

```bash
ucloud config list
```

### 更新 Profile 配置

```bash
ucloud config update --profile <name> --region <region>
```

> 建议：内建命令（`ucloud uhost`、`ucloud udisk` 等）显式指定 `--profile`，不依赖 active profile。
> 例外：`ucloud api` 不支持 `--profile` flag，需要提前通过 `ucloud config update --profile <name> --active true` 切换 active profile。

## 验证

```bash
# 检查版本
ucloud version

# 查看可用区域
ucloud region

# 查看项目列表
ucloud project list

# 查看云主机列表
ucloud uhost list
```

## Shell 自动补全

### Bash

在 `~/.bash_profile` 中添加：

```bash
complete -C $(which ucloud) ucloud
```

### Zsh

在 `~/.zshrc` 中添加：

```bash
autoload -U +X bashcompinit && bashcompinit
complete -C $(which ucloud) ucloud
```

## 常见错误排查

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `command not found: ucloud` | 未安装或不在 PATH | `brew install ucloud` 或下载二进制 |
| `authentication failed` | PublicKey/PrivateKey 错误 | 重新配置：`ucloud init` 或 `ucloud config update` |
| `project not found` | ProjectID 错误或无权限 | `ucloud project list` 确认可用项目 |
| `region not found` | 区域代码错误 | `ucloud region` 查看可用区域 |
| `you are not authorized` | 子账号权限不足 | 联系主账号管理员授权 |
| `network timeout` | API 端点不可达 | 检查网络或指定 `--base-url` |
