# AWS CLI 安装与配置

## 安装

### macOS

```bash
brew install awscli
```

### Windows

```powershell
# winget
winget install Amazon.AWSCLI

# 或 MSI 安装器
# https://awscli.amazonaws.com/AWSCLIV2.msi
```

### Linux

```bash
# 方式一：官方安装脚本（推荐）
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# 方式二：pip
pip install awscli
```

## 认证配置

### 配置 Profile

```bash
aws configure --profile <profileName>
```

按提示输入：
- AWS Access Key ID
- AWS Secret Access Key
- Default region name（如 us-east-1）
- Default output format（建议 json）

### 查看已有 Profile

```bash
aws configure list-profiles
```

### 验证当前身份

```bash
aws sts get-caller-identity --profile <profileName>
```

> 建议：所有命令显式指定 `--profile`，不依赖默认 profile。

## 验证

```bash
# 检查版本
aws --version

# 测试 API 调用
aws sts get-caller-identity --profile <profileName>
```

## 常见错误排查

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `command not found: aws` | 未安装或不在 PATH | 按上方安装步骤安装 |
| `InvalidClientTokenId` | Access Key ID 无效 | 重新配置：`aws configure --profile <name>` |
| `SignatureDoesNotMatch` | Secret Access Key 错误 | 重新配置 profile |
| `ExpiredToken` | 临时凭证过期 | 重新获取 STS token 或重新 SSO 登录 |
| `AccessDenied` | IAM 权限不足 | 联系管理员授权对应 API 权限 |
| `The config profile (xxx) could not be found` | 指定的 profile 不存在 | `aws configure list-profiles` 查看已有 profile |
| 参考 profile 不存在或没有凭据，但存在同账户别名 | 本机 profile 命名与共享参考名不同，或同一账户跨区域复用凭据 | 对合理候选执行 `sts get-caller-identity`；账户匹配后使用该 profile，并显式传入目标 `--region`。不要复制/重配长期凭据 |
