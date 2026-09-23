# Azure CLI 安装与配置

## 安装

### macOS

```bash
brew install azure-cli
```

### Windows

```powershell
# winget（推荐）
winget install Microsoft.AzureCLI

# 或 MSI 安装器
# https://aka.ms/installazurecliwindows
```

### Linux

```bash
# Debian / Ubuntu（推荐）
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# RHEL / CentOS / Fedora
sudo rpm --import https://packages.microsoft.com/keys/microsoft.asc
sudo dnf install azure-cli

# pip
pip install azure-cli
```

## 认证配置

### 登录

```bash
# 交互式登录（浏览器）
az login

# 设备代码登录（无浏览器环境）
az login --use-device-code

# Service Principal 登录
az login --service-principal -u <app-id> -p <password-or-cert> --tenant <tenant-id>
```

### 查看当前账户

```bash
az account show
```

### 查看所有可用订阅

```bash
az account list --output table
```

### 切换订阅

```bash
az account set --subscription <subscription-id-or-name>
```

> 注意：Azure CLI 同一时间只能登录一个账户。切换账户需先 `az logout` 再 `az login`。不同于 AWS/GCP 的 profile 机制。

## 验证

```bash
# 检查版本
az version

# 查看当前登录账户
az account show

# 测试 API 调用（列出资源组）
az group list --output table
```

## 常见错误排查

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `command not found: az` | 未安装或不在 PATH | 按上方安装步骤安装 |
| `Please run 'az login' to setup account` | 未登录或 token 过期 | 执行 `az login` 重新登录 |
| `The subscription of 'xxx' doesn't exist` | 订阅 ID 错误或无权访问 | `az account list` 查看可用订阅 |
| `AuthorizationFailed` | 当前用户无权操作该资源 | 联系管理员授权 RBAC 角色 |
| `ResourceGroupNotFound` | 资源组名称错误 | `az group list` 查看可用资源组 |
| `InteractionRequired` | 需要重新认证（MFA 等） | 执行 `az login` 重新登录 |
