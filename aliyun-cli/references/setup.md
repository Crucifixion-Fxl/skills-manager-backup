# aliyun CLI 安装与配置

## 安装

> Mac `sre-executor` 是受管运行环境，不执行本页的安装、`aliyun configure` 或 profile
> 排障。该角色只能使用 runtime 提供的 `./sre-platform-exec` 和固定 target；wrapper
> 缺失或拒绝时 fail closed，由管理员按部署合同修复。以下步骤仅用于普通终端环境。

### macOS

```bash
brew install aliyun-cli
```

### Windows

```powershell
# Scoop
scoop install aliyun-cli

# 或从 GitHub Releases 下载
# https://github.com/aliyun/aliyun-cli/releases
```

### Linux

```bash
# 方式一：curl 下载（推荐）
curl -fsSL https://aliyuncli.alicdn.com/aliyun-cli-latest-linux-amd64.tgz | tar xz
sudo mv aliyun /usr/local/bin/

# 方式二：pip
pip install aliyun-cli
```

## 认证配置

### 配置 Profile（AK 模式）

```bash
aliyun configure --profile <profileName> --mode AK
```

按提示输入：
- Access Key ID
- Access Key Secret
- Default Region（如 cn-hangzhou）
- Default Language（建议 en）

### 查看已有 Profile

```bash
aliyun configure list
```

### 切换默认 Profile

```bash
aliyun configure switch --profile <profileName>
```

> 建议：所有命令显式指定 `--profile`，不依赖默认 profile。

## 验证

```bash
# 检查版本
aliyun version

# 测试 API 调用（列出区域）
aliyun ecs DescribeRegions --profile <profileName>
```

## 常见错误排查

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `command not found: aliyun` | 未安装或不在 PATH | 按上方安装步骤安装 |
| `InvalidAccessKeyId` | AccessKey ID 错误 | 重新配置：`aliyun configure --profile <name> --mode AK` |
| `SignatureDoesNotMatch` | AccessKey Secret 错误 | 重新配置 profile |
| `No profile found` | 指定的 profile 不存在 | `aliyun configure list` 查看已有 profile |
| `Forbidden.RAM` | RAM 子账号权限不足 | 联系管理员授权对应 API 权限 |
