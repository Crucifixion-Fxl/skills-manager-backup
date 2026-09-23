---
name: firmware-build
description: Build camera firmware on remote Linux build server or locally via Docker. Use when the user mentions firmware build, building camera firmware, total_build.sh, cross-compilation, MCU firmware, or wants to compile embedded firmware for any camera product. Also trigger when user says "build firmware", "compile firmware", "construct firmware".
---

# Firmware Build Skill

Build camera firmware (<firmware_repo>) via two methods:
- **Remote build** — SSH to a Linux build server with pre-installed toolchains
- **Local Docker build** — Run on any machine (macOS/Windows/Linux) with Docker

## Description

本 Skill 引导 AI Agent 完成 <firmware_repo> 固件的端到端构建，支持多款摄像头产品、多个 SOC 平台（MIPS/ARM/RISC-V）。支持远程 SSH 构建和本地 Docker 构建两种模式。

**适用场景：**
- 用户需要构建摄像头固件
- 新人首次搭建固件构建环境
- 需要在本地 macOS/Windows/Linux 上通过 Docker 构建固件

### First-Time Setup Checklist

Before building, ask the user for this information if not already known:

1. **Build method** — ask "Do you want to build remotely (SSH to a server) or locally (Docker)?"
2. **Product model** — which camera to build. Ask "Which product model do you want to build?" Supported models can be found by running `ls config/` in the repo root, or by running `docker run --rm fw-builder` (Docker method).
3. **GitLab access** — the user's GitLab account must have access to all submodule repos. If submodule init fails with "permission denied", tell the user to request access from their GitLab admin.

For **remote build** (Method A), also ask:
4. **Build server address** — ask "Which build server should I use? (e.g. user@host)"
5. **Build server password** — needed for SSH key setup and sudo config, ask "What's the server password?" (never store this in files or logs)

## 执行流程

### Method A: Remote Build (SSH to Linux Server)

Follow these steps in order. Check each step's precondition before executing — skip if already done.

#### Step 1: SSH Connection Setup

**Check**: `ssh <user>@<host> "echo OK"`

If it fails with "Permission denied":
1. Check if the user has an SSH key: `ls ~/.ssh/id_rsa.pub`
2. If no key exists: `ssh-keygen -t rsa -b 4096` (press Enter for all prompts)
3. Copy key to server: `ssh-copy-id <user>@<host>` (enter the server password when prompted)
4. Verify: `ssh <user>@<host> "echo OK"`

#### Step 2: SSH Agent Forwarding Setup

The build server doesn't have GitLab SSH keys — your local keys must be forwarded via `ssh -A`. This is needed for cloning repos and initializing submodules.

```bash
# 1. Check if ssh-agent has keys loaded
ssh-add -l

# 2. If "The agent has no identities", add your key:
ssh-add ~/.ssh/id_rsa

# 3. Verify forwarding works end-to-end:
ssh -A <user>@<host> "ssh -T git@<gitlab_host>"
# Expected: "Welcome to GitLab, @<username>!"
```

If step 3 shows "Host key verification failed", fix it:
```bash
ssh <user>@<host> "ssh-keyscan <gitlab_host> >> ~/.ssh/known_hosts 2>/dev/null"
```

**Important**: All `git clone` and `git submodule` commands on the remote server MUST use `ssh -A` for agent forwarding. Regular `ssh` (without `-A`) will fail with "Permission denied (publickey)" on GitLab operations. However, non-git commands (build, check tools, etc.) can use plain `ssh`.

#### Step 3: Clone Repository

**Check**: `ssh <user>@<host> "cd ~/<firmware_repo> && git log --oneline -1 2>/dev/null && echo EXISTS || echo NOT_EXISTS"`

If NOT_EXISTS — ask the user for the GitLab repo URL, then:
```bash
ssh -A <user>@<host> "git clone <repo_url> ~/<firmware_repo>"
```

If the directory exists but is broken (e.g. previous failed clone), remove and re-clone:
```bash
ssh <user>@<host> "rm -rf ~/<firmware_repo>"
ssh -A <user>@<host> "git clone <repo_url> ~/<firmware_repo>"
```

Note: This repo is very large (~12GB with full history). The clone may take 10+ minutes.

#### Step 4: Initialize All Submodules

**Check**: verify that `.gitmodules` listed submodule directories exist and are non-empty:
```bash
ssh <user>@<host> "cd ~/<firmware_repo> && git submodule status 2>/dev/null | head -5"
```
If submodules show `-` prefix (uninitialized), run:
```bash
ssh -A <user>@<host> "cd ~/<firmware_repo> && git submodule update --init --recursive"
```

If any submodule fails with "could not be found or you don't have permission", the user needs to request access to the corresponding GitLab group from their admin. **Do not proceed** — the build will fail without all submodules.

#### Step 5: Install Build Dependencies

**Check**: `ssh <user>@<host> "cd ~/<firmware_repo> && bash check_tools.sh 2>&1 | tail -5"`

If missing tools are reported:

**Python packages** (the `LC_ALL=C` prevents locale errors on some systems):
```bash
ssh <user>@<host> "export LC_ALL=C && pip3 install six pycryptodome ecdsa"
```

**sudo access** — the build uses sudo for mksquashfs/mkimage to create filesystem images:
```bash
ssh <user>@<host> "sudo echo OK 2>/dev/null && echo SUDO_OK || echo NEED_SUDO"
```
If NEED_SUDO, configure passwordless sudo (requires the server password):
```bash
ssh -t <user>@<host> "echo '<server_password>' | sudo -S bash -c 'echo \"<username> ALL=(ALL) NOPASSWD: ALL\" > /etc/sudoers.d/<username> && chmod 440 /etc/sudoers.d/<username>'"
```

#### Step 6: Environment Configuration

**Check Python version** — some build scripts require Python 3.6+:
```bash
ssh <user>@<host> "python3 --version"
```

If Python is 3.5.x (common on Ubuntu 16.04), some SDK scripts use f-strings which will fail. Fix by converting f-strings to `.format()` syntax — create a fixer script, upload via scp, and run it on the affected files.

**Check RISC-V toolchain** — required for WiFi chip compilation (most products use this):
```bash
ssh <user>@<host> "which riscv32-unknown-elf-gcc 2>/dev/null || find /opt -name 'riscv32-unknown-elf-gcc' 2>/dev/null | head -1"
```
If found, note the path for Step 7. If not found, the toolchain must be installed on the build server — contact the infra team.

#### Step 7: Build Firmware

Combine all environment variables into a single build command. The build takes 10-30 minutes depending on the product and server performance. Use the RISC-V toolchain path discovered in Step 6.

```bash
ssh <user>@<host> "export PATH=<riscv_toolchain_path>:\$PATH && export PYTHONIOENCODING=utf-8 && export LC_ALL=en_US.UTF-8 && cd ~/<firmware_repo> && bash total_build.sh <PRODUCT_MODEL> 2>&1"
```

The build is long-running — use the Bash tool's `run_in_background` parameter to avoid blocking, then check with `TaskOutput`.

Replace `<PRODUCT_MODEL>` with the target in **uppercase**.

**Build command variants** (for subsequent builds after the first full build):
| Command | Use Case | Speed |
|---------|----------|-------|
| `total_build.sh <MODEL>` | First build or clean rebuild | Slow (full clean + build) |
| `build.sh all <MODEL> release` | Full rebuild without clean | Medium |
| `build.sh app <MODEL>` | Changed application code only | Fast |
| `build.sh mcu <MODEL>` | Changed MCU firmware only | Fast |

#### Step 8: Verify Build Output

```bash
ssh <user>@<host> "ls ~/<firmware_repo>/out/<model_lowercase>/upgrade.fw 2>/dev/null && echo BUILD_SUCCESS || echo BUILD_FAILED"
```

Note: `<model_lowercase>` is the product model in lowercase.

**Primary deliverables** — all platforms generate these two firmware packages:
| File | Description |
|------|-------------|
| `upgrade.fw` | Release firmware package (OTA upgrade file) |
| `debug_upgrade.fw` | Debug firmware package |

To download firmware to local machine:
```bash
mkdir -p ./firmware_output
scp <user>@<host>:~/<firmware_repo>/out/<model_lowercase>/upgrade.fw ./firmware_output/<MODEL>_upgrade.fw
scp <user>@<host>:~/<firmware_repo>/out/<model_lowercase>/debug_upgrade.fw ./firmware_output/<MODEL>_debug_upgrade.fw
```

### Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `riscv32-unknown-elf-gcc: command not found` | RISC-V toolchain not in PATH | Find toolchain path with `find /opt -name 'riscv32-unknown-elf-gcc'` and add to PATH |
| `SyntaxError: invalid syntax` on f-string | Python < 3.6 doesn't support f-strings | Convert f-strings to `.format()` syntax (Step 6) |
| `UnicodeEncodeError: 'ascii' codec` | Terminal locale doesn't support Chinese chars | `export PYTHONIOENCODING=utf-8 LC_ALL=en_US.UTF-8` |
| `sudo: no tty present` | Build needs sudo for mkfs operations | Configure passwordless sudo (Step 5) |
| `Permission denied (publickey)` on submodule | No GitLab access or forgot `ssh -A` | Use `ssh -A` for git commands; request GitLab group access |
| `Host key verification failed` | GitLab host key not trusted on build server | `ssh <user>@<host> "ssh-keyscan <gitlab_host> >> ~/.ssh/known_hosts"` |
| `destination path already exists` | Previous failed clone left empty dir | Remove with `rm -rf ~/<firmware_repo>` then re-clone |
| `Connection reset by peer` during clone | Repo too large, connection dropped | Use `git clone --depth 1` for shallow clone |

---

### Method B: Local Docker Build (macOS / Windows / Linux)

This method runs the build inside a Docker container, so no remote server is needed. The Docker image contains all cross-compilation toolchains (~3GB).

#### Prerequisites
- Docker Desktop installed and running
- <firmware_repo> repo cloned locally with all submodules initialized

#### One-Time Setup: Build the Docker Image

The toolchains must be exported from a build server first (only needed once):

```bash
cd <<firmware_repo>_root>

# Step 1: Export toolchains from build server (~3GB download)
./docker/export_toolchains.sh <user>@<host>

# Step 2: Build Docker image
docker build -t fw-builder -f docker/Dockerfile docker/
```

This creates a `fw-builder` Docker image with all toolchains and dependencies pre-installed.

#### Build Firmware with Docker

```bash
cd <<firmware_repo>_root>

# Full build
docker run --rm -v $(pwd):/workspace fw-builder <MODEL>

# Release only
docker run --rm -v $(pwd):/workspace fw-builder <MODEL> all release

# App layer only (fast rebuild)
docker run --rm -v $(pwd):/workspace fw-builder <MODEL> app

# Show supported models
docker run --rm fw-builder
```

Build output is in `out/<model_lowercase>/` — same as remote build.

#### Docker vs Remote Build Comparison

| Aspect | Remote SSH | Local Docker |
|--------|-----------|--------------|
| Setup complexity | Low (server ready) | Medium (export toolchains + build image) |
| Build speed | Fast (multi-core server) | Depends on local machine |
| Disk space | None locally | ~3GB for image + build output |
| Network dependency | Needs SSH access | None after setup |

## 示例

### Good

```
用户："帮我构建固件，服务器是 john@192.168.1.100"
AI：先问用户要构建哪个型号 → Step 1 检查 SSH → Step 2 配置 agent forwarding
→ Step 3 检查仓库 → Step 4 检查子模块 → Step 5 检查依赖 → Step 6 检查工具链
→ Step 7 后台执行 total_build.sh <MODEL> → Step 8 验证 upgrade.fw → scp 下载到本地
```

```
用户："用 Docker 在本地构建固件"
AI：先问用户要构建哪个型号 → 检查 Docker 是否运行 → 检查 fw-builder 镜像是否存在
→ docker run --rm -v $(pwd):/workspace fw-builder <MODEL>
→ 构建完成后检查 out/<model>/upgrade.fw
```

### Bad

```
用户："构建固件"
AI：直接执行 total_build.sh，没有先确认产品型号和构建方式
→ 应该先问用户要构建哪个型号、用远程还是 Docker
```

```
用户："构建固件"
AI：用 ssh（不带 -A）在远程执行 git submodule update
→ 报 Permission denied (publickey) 错误
→ 应该用 ssh -A 进行 agent forwarding
```
