# tccli 安装与配置

## 安装

### macOS / Linux / Windows

```bash
# pip 安装（推荐，跨平台通用）
pip install tccli

# 或 pip3
pip3 install tccli
```

> 要求 Python 2.7 或 3.6+。建议使用 Python 3。

### 升级

```bash
pip install --upgrade tccli
```

## 认证配置

### 配置 Profile

```bash
# 配置默认 profile
tccli configure

# 配置指定 profile
tccli configure --profile <profileName>
```

按提示输入：
- SecretId
- SecretKey
- Region（如 ap-beijing）
- Output（建议 json）

### 查看已有 Profile

```bash
# 列出所有 profile 配置文件
ls ~/.tccli/*.configure | xargs -n1 basename | sed 's/.configure$//'
```

> 注意：tccli 没有内置的 profile 列表命令，需通过文件名推断。

### 切换 Profile

tccli 没有内置的 switch 命令，建议始终在命令中显式指定 `--profile`。

## 验证

```bash
# 检查版本
tccli --version

# 测试 API 调用（查看地域列表）
tccli cvm DescribeRegions --profile <profileName>
```

## 常见错误排查

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `command not found: tccli` | 未安装或不在 PATH | `pip install tccli`；检查 pip 安装路径是否在 PATH 中 |
| `AuthFailure.SecretIdNotFound` | SecretId 错误 | 重新配置：`tccli configure --profile <name>` |
| `AuthFailure.SignatureFailure` | SecretKey 错误 | 重新配置 profile |
| `AuthFailure.TokenFailure` | 临时凭证过期 | 重新获取临时凭证 |
| `UnauthorizedOperation` | CAM 子账号权限不足 | 联系管理员授权对应 API 权限 |
| `InvalidParameter` | 参数格式错误 | `tccli <Service> <Action> --help` 查看参数说明 |
| `UnsupportedRegion` | 不支持的区域 | 检查区域代码是否正确（如 `ap-beijing`） |
