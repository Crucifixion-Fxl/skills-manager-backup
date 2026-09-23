# OCI CLI 安装与配置

## 安装

### macOS

```bash
brew install oci-cli
```

### Linux

```bash
# pip（推荐）
pip install oci-cli

# 或官方安装脚本（备选，远程脚本安装存在供应链风险，建议先下载审查后再执行）
bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"
```

### Windows

```powershell
# pip（推荐）
pip install oci-cli

# 或官方安装脚本（以管理员运行，-ExecutionPolicy Bypass 仅在本次安装过程中生效，不会永久更改系统策略）
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.ps1'))"
```

> Windows 用户安装完成后需要重启终端，并确认 `oci` 在 PATH 中。

## 认证配置

`oci setup config` 是交互式命令，无法在自动化环境中运行。以下为手动配置流程。

### 前置信息收集

需要从 Oracle Cloud Console 获取以下信息：

| 信息 | 获取路径 |
|------|---------|
| User OCID | Console → Profile（右上角）→ My profile → OCID |
| Tenancy OCID | Console → Profile → Tenancy → OCID |
| Home Region | Console → Infrastructure → Regions，带 🏠 标记的即为 Home Region |

### 步骤 1：生成 API Signing Key Pair

**macOS / Linux：**

```bash
mkdir -p ~/.oci && openssl genrsa -out ~/.oci/oci_api_key.pem 2048 && chmod 600 ~/.oci/oci_api_key.pem && openssl rsa -pubout -in ~/.oci/oci_api_key.pem -out ~/.oci/oci_api_key_public.pem
```

**Windows (PowerShell)：**

```powershell
$ociDir = "$env:USERPROFILE\.oci"
if (!(Test-Path $ociDir)) { New-Item -ItemType Directory -Path $ociDir }
openssl genrsa -out "$ociDir\oci_api_key.pem" 2048
openssl rsa -pubout -in "$ociDir\oci_api_key.pem" -out "$ociDir\oci_api_key_public.pem"
```

> Windows 如果没有 openssl，可通过 `winget install ShiningLight.OpenSSL` 或 Git Bash 自带的 openssl 来执行。

### 步骤 2：获取 Fingerprint

**macOS / Linux：**

```bash
openssl rsa -pubout -outform DER -in ~/.oci/oci_api_key.pem 2>/dev/null | openssl md5 -c | awk '{print $2}'
```

**Windows (PowerShell)：**

```powershell
openssl rsa -pubout -outform DER -in "$env:USERPROFILE\.oci\oci_api_key.pem" 2>$null | openssl md5 -c
```

### 步骤 3：创建配置文件

配置文件路径：
- macOS / Linux: `~/.oci/config`
- Windows: `%USERPROFILE%\.oci\config`

```ini
[oci-a4x-us-prod]
user=ocid1.user.oc1..xxxxx
fingerprint=xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx
tenancy=ocid1.tenancy.oc1..xxxxx
region=<home-region-identifier>
key_file=~/.oci/oci_api_key.pem
```

> Windows 用户 `key_file` 使用反斜杠路径，如 `C:\Users\<username>\.oci\oci_api_key.pem`。

### 步骤 4：上传公钥到 Oracle Cloud Console

1. 打开 Oracle Cloud Console → Profile → My profile → API keys → Add API key
2. 选择 "Paste a public key"
3. 粘贴 `oci_api_key_public.pem` 的内容：
   - macOS / Linux: `cat ~/.oci/oci_api_key_public.pem`
   - Windows: `Get-Content "$env:USERPROFILE\.oci\oci_api_key_public.pem"`
4. 点击 Add

## 验证

```bash
# 检查版本
oci --version

# 测试 API 调用（列出区域）
oci iam region list --profile oci-a4x-us-prod --output table
```

如果返回区域列表，说明配置成功。

## 常见错误排查

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `command not found: oci` | 未安装或不在 PATH | 按上方安装步骤安装；Windows 需重启终端 |
| `ConfigFileNotFound` | 配置文件不存在 | 按上方步骤创建 `~/.oci/config` |
| `ProfileNotFound` | 指定的 profile 不存在 | 检查 `~/.oci/config` 中的 section 名称 |
| `InvalidKeyFilePath` | key_file 路径错误 | 确认 `~/.oci/oci_api_key.pem` 存在且路径正确 |
| `NotAuthenticated` / `401` | API key 未上传或 fingerprint 不匹配 | 重新上传公钥到 Console，确认 fingerprint 一致 |
| `NotAuthorizedOrNotFound` / `404` | 无权访问或资源不存在 | 确认 compartment-id 正确，联系管理员授权 |
| `ServiceError: 429` | 请求频率过高 | 稍后重试，或减少并发请求 |
