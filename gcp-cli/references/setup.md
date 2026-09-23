# gcloud CLI 安装与配置

## 安装

### macOS

```bash
brew install google-cloud-sdk
```

### Windows

```powershell
# 官方安装器（推荐）
# 下载地址：https://cloud.google.com/sdk/docs/install#windows
# 运行 GoogleCloudSDKInstaller.exe

# 或通过 PowerShell
(New-Object Net.WebClient).DownloadFile("https://dl.google.com/dl/cloudsdk/channels/rapid/GoogleCloudSDKInstaller.exe", "$env:TEMP\GoogleCloudSDKInstaller.exe")
& "$env:TEMP\GoogleCloudSDKInstaller.exe"
```

### Linux

```bash
# Snap（推荐）
snap install google-cloud-cli --classic

# Debian / Ubuntu
echo "deb [signed-by=/usr/share/keyrings/cloud.google.asc] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo tee /usr/share/keyrings/cloud.google.asc
sudo apt-get update && sudo apt-get install google-cloud-cli

# RHEL / CentOS / Fedora
sudo tee /etc/yum.repos.d/google-cloud-sdk.repo << 'EOF'
[google-cloud-cli]
name=Google Cloud CLI
baseurl=https://packages.cloud.google.com/yum/repos/cloud-sdk-el9-x86_64
enabled=1
gpgcheck=1
repo_gpgcheck=0
gpgkey=https://packages.cloud.google.com/yum/doc/rpm-package-key.gpg
EOF
sudo dnf install google-cloud-cli
```

## 认证配置

### 登录

```bash
# 交互式登录（浏览器）
gcloud auth login

# 无浏览器环境
gcloud auth login --no-launch-browser
```

### 查看当前登录账户

```bash
gcloud auth list
```

### 设置默认项目

```bash
gcloud config set project <project-id>
```

> 建议：所有命令显式指定 `--project`，不依赖默认项目。

### Application Default Credentials（ADC）

```bash
# 用于需要 ADC 的 SDK/应用
gcloud auth application-default login
```

## 验证

```bash
# 检查版本
gcloud version

# 查看当前认证状态
gcloud auth list

# 测试 API 调用
gcloud projects list --format="table(projectId,name,projectNumber)"
```

## 常见错误排查

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `command not found: gcloud` | 未安装或不在 PATH | 按上方安装步骤安装；安装后执行 `source ~/.bashrc` 或重启终端 |
| `You do not currently have an active account selected` | 未登录 | 执行 `gcloud auth login` |
| `The project property is set to the empty string` | 未设置默认项目 | `gcloud config set project <project-id>` 或命令中指定 `--project` |
| `PERMISSION_DENIED` | 当前用户无权操作该资源 | 联系管理员授权 IAM 角色 |
| `HttpError 403` | API 未启用或权限不足 | 确认项目中已启用对应 API：`gcloud services enable <api>` |
| `Could not load the default credentials` | ADC 未配置 | 执行 `gcloud auth application-default login` |
