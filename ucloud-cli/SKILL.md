---
name: ucloud-cli
description: 通过 UCloud CLI 管理 UCloud 云资源。当用户提到 UCloud、优刻得、UHost、GPU 云主机、UDisk、EIP、ULB、UDB、UMem、VPC、UNet、US3、防火墙、UCloud 账单，或需要查询/操作 UCloud 云资源时使用。即使用户只说"看看 UCloud 的机器"、"查一下 GPU 实例"、"UCloud 这个月花了多少"也应触发。
---

# ucloud-cli

## Description

通过 UCloud 官方 CLI（ucloud）协助运维和开发人员管理 UCloud 云资源。当前主要用途为 AI 训练（GPU 云主机）。

## 账户信息

| 项目 | 值 |
|------|-----|
| 用途 | AI 训练 |
| 账户数 | 1 |
| CLI Profile | 登录后通过 `ucloud config list` 确认 |

账户和 Project ID 在首次使用时通过 `ucloud config list` 获取并记录。

### 区域速查

首次使用时执行 `ucloud region` 扫描可用区域，后续按实际使用区域操作。

## 认证方式

通过 PublicKey + PrivateKey 鉴权，配置存储在 `~/.ucloud/` 目录下（`config.json` + `credential.json`）。

```bash
# 首次初始化（交互式，自动创建 default profile）
ucloud init

# 添加新 profile
ucloud config add --profile <name> --public-key <key> --private-key <key>

# 查看已有 profile
ucloud config list

# 更新 profile 配置
ucloud config update --profile <name> --region <region>
```

所有命令建议显式指定 `--profile`，避免依赖 active profile。

## CLI 基础用法

UCloud CLI 有两种调用方式：

### 1. 内建命令（常用服务）

```bash
ucloud <service> <action> [--profile <profile>] [--project-id <id>] [--region <region>] [--zone <zone>] [flags]
```

### 2. 通用 API 调用（任意服务）

CLI 未内建的服务（如账单、US3、UAI-Train 等）通过 `ucloud api` 调用。

**重要**：`ucloud api` 只接受 `--Action` 和 API 参数键值对，**不支持 `--profile`、`--json` 等 CLI flag**（会被当作 API 参数解析导致报错）。需要提前通过 `ucloud config` 设置 active profile。

```bash
# 正确
ucloud api --Action GetBalance
ucloud api --Action ListUBillDetail --BillingCycle 2026-04 --Limit 100

# 错误 — --profile 和 --json 会导致参数解析报错
ucloud api --Action GetBalance --profile default    # ❌
ucloud api --Action GetBalance --json               # ❌
```

使用 `ucloud api` 时，**必须先读取 `references/api-reference.md`** 查找正确的 Action 名称和参数，不要猜测。

常用全局 flag（用于内建命令，非 `ucloud api`）：
- `--profile`：指定配置 profile
- `--project-id`：覆盖默认项目
- `--region`：覆盖默认区域
- `--zone`：覆盖默认可用区

## 常用服务速查

### 内建命令

| 服务 | CLI 命令 | 说明 |
|------|---------|------|
| 云主机 | `ucloud uhost` | UHost 实例管理（含 GPU 机型） |
| 物理云主机 | `ucloud uphost` | 裸金属服务器 |
| 云硬盘 | `ucloud udisk` | UDisk 磁盘管理 |
| 弹性 IP | `ucloud eip` | EIP 分配/绑定/释放 |
| 负载均衡 | `ucloud ulb` | ULB 实例与后端管理 |
| 云数据库 MySQL | `ucloud mysql` | MySQL 实例、备份、日志 |
| 云内存 Redis | `ucloud redis` | Redis 实例管理 |
| 云内存 Memcache | `ucloud memcache` | Memcache 实例管理 |
| VPC | `ucloud vpc` | 私有网络管理 |
| 子网 | `ucloud subnet` | 子网管理 |
| 防火墙 | `ucloud firewall` | 安全组规则 |
| 镜像 | `ucloud image` | 自定义镜像管理 |
| 项目 | `ucloud project` | 项目管理 |
| GlobalSSH | `ucloud gssh` | 跨境 SSH 加速 |
| 带宽包 | `ucloud bw` | 共享带宽管理 |
| 区域 | `ucloud region` | 查看可用区域/可用区 |

### 通用 API 调用（ucloud api）— 常用 Action

使用前先读取 `references/api-reference.md` 获取完整参数说明。

| 场景 | Action | 示例 |
|------|--------|------|
| 账户余额 | `GetBalance` | `ucloud api --Action GetBalance` |
| 账单总览 | `ListUBillOverview` | `ucloud api --Action ListUBillOverview --BillingCycle 2026-04 --Dimension product` |
| 账单明细 | `ListUBillDetail` | `ucloud api --Action ListUBillDetail --BillingCycle 2026-04 --Limit 100` |
| 下载账单文件 | `GetBillDataFileUrl` | `ucloud api --Action GetBillDataFileUrl --BillingCycle 2026-04 --BillType 1` |
| 订单明细 | `DescribeOrderDetailInfo` | `ucloud api --Action DescribeOrderDetailInfo --BeginTime <ts> --EndTime <ts>` |
| 详细实例信息 | `DescribeUHostInstance` | `ucloud api --Action DescribeUHostInstance --Limit 100` |
| GPU 机型询价 | `GetUHostInstancePrice` | `ucloud api --Action GetUHostInstancePrice --CPU 8 --Memory 32768 --MachineType G --GpuType V100 --GPU 1` |
| 查看 US3 存储桶 | `DescribeBucket` | `ucloud api --Action DescribeBucket` |

## 执行流程

### Step 0: 确保 CLI 可用

首次执行前检查 `ucloud` 是否已安装（`which ucloud`）。如果未安装：

1. 读取 `references/setup.md` 获取安装步骤
2. 告知用户即将安装 UCloud CLI，确认后直接执行安装命令（如 `brew install ucloud`）
3. 安装完成后，如果尚未配置 profile，执行 `ucloud init` 引导用户完成鉴权配置（需要用户提供 PublicKey / PrivateKey）

后续如果遇到认证失败、配置缺失等错误，同样读取 `references/setup.md` 按步骤直接修复，每步操作前向用户确认。

### Step 1: 确认操作目标

根据用户描述确定：

1. **目标 Profile**：确定 `--profile`
   - 先执行 `ucloud config list` 查看已有 profile
   - 如果只有一个 active profile，直接使用
   - 如果有多个 profile，且根据上下文无法判断，主动询问用户
2. **目标区域**：未指定时使用 profile 配置的默认 region，用户指定其他区域时用 `--region` 覆盖
3. **操作内容**：判断读/写操作

### Step 2: 执行命令

**内建命令**显式指定 `--profile`，不要依赖 active profile：

```bash
ucloud <service> <action> --profile <profile> [--region <region>] [flags]
```

**通用 API**（`ucloud api`）不支持 `--profile` flag，需要提前通过 `ucloud config` 设置 active profile，然后直接调用：

```bash
# 如需切换 profile，先设置
ucloud config update --profile <name> --active true

# 再调用 API（不带 --profile）
ucloud api --Action <APIName> --Param1 <value> --Param2 <value>
```

如不确定内建命令参数，可查看帮助：

```bash
ucloud <service> <action> --help
```

## Rules

### 查询范围约束

**默认单 profile 查询**：除非用户明确要求（如"查看所有项目的资源"），否则只在单个 profile 中查询资源，不要自动遍历所有 profile。

### 操作分级

#### 只读操作（直接执行）

所有 `list`、`describe` 类子命令和 `Describe*`、`Get*` 类 API Action 均为只读，可直接执行：

- **UHost**: `ucloud uhost list`
- **UPHost**: `ucloud uphost list`
- **UDisk**: `ucloud udisk list`, `ucloud udisk list-snapshot`
- **EIP**: `ucloud eip list`
- **ULB**: `ucloud ulb list`, `ucloud ulb vserver list`, `ucloud ulb vserver backend list`
- **MySQL**: `ucloud mysql db list`, `ucloud mysql backup list`, `ucloud mysql logs list`, `ucloud mysql conf list`
- **Redis**: `ucloud redis list`
- **Memcache**: `ucloud memcache list`
- **VPC**: `ucloud vpc list`, `ucloud vpc list-intercome`
- **Subnet**: `ucloud subnet list`, `ucloud subnet list-resource`
- **Firewall**: `ucloud firewall list`, `ucloud firewall resource`
- **Image**: `ucloud image list`
- **Project**: `ucloud project list`
- **Region**: `ucloud region`
- **通用 API**: `ucloud api --Action Describe*`, `ucloud api --Action Get*`, `ucloud api --Action List*`

#### 高危操作（必须用户确认）

以下操作会修改资源状态，**必须在执行前向用户展示完整命令并获得明确确认**：

- **创建资源**: `create`, `clone`, `allocate`, `ucloud api --Action Create*`
- **修改资源**: `resize`, `reinstall-os`, `reset-password`, `update`, `modify-bw`, `ucloud api --Action Modify*`
- **删除资源**: `delete`, `release`, `ucloud api --Action Delete*`, `Terminate*`
- **启停操作**: `start`, `stop`, `poweroff`, `restart`, `reboot`
- **磁盘操作**: `attach`, `detach`, `expand`, `snapshot`, `restore`
- **网络变更**: `bind`, `unbind`, `firewall add-rule`, `firewall remove-rule`, `firewall apply`
- **数据库操作**: `ucloud mysql db create-slave`, `promote-slave`, `restore`

确认格式：

```
⚠️ 即将执行写操作：

  Profile: <profile-name>
  区域: <region>
  命令: ucloud uhost stop --uhost-id uhost-xxx --profile <profile>

确认执行？(y/n)
```

### 操作红线

1. **禁止未确认的写操作** — 任何写操作都必须二次确认
2. **禁止暴露密钥** — PrivateKey、数据库密码等只展示前 4 位 + `***`
3. **禁止大范围删除** — 批量 delete/release 必须先列出受影响资源并确认
4. **US3 数据操作** — `ucloud api` 不支持 US3 数据上传下载，需使用 `us3cli`，提醒用户
5. **费用查询** — 默认查询最近一个月，避免拉取过大时间范围

### 输出优化

- 内建命令默认以表格形式输出，可读性较好
- `ucloud api` 返回 JSON，使用 `jq` 过滤关键字段
- 对于大量结果，注意 API 分页参数（`Offset` / `Limit`）

## Examples

### Good

```
# 查看 GPU 云主机
用户：看看 UCloud 上的 GPU 机器

AI：查询 UHost 实例：
  [执行 ucloud uhost list --profile <profile>]
  找到以下 GPU 实例...
```

```
# 写操作确认
用户：帮我停掉那台训练机

AI：找到实例：
  uhost-xxxx (gpu-training-01) - Running

  ⚠️ 即将执行写操作：

    Profile: default
    区域: cn-bj2
    命令: ucloud uhost stop --uhost-id uhost-xxxx --profile default

  确认执行？(y/n)
```

```
# 通用 API 调用
用户：查一下 UCloud 账单

AI：通过通用 API 查询账单（先读取 references/api-reference.md 确认参数）：
  [执行 ucloud api --Action ListUBillDetail --BillingCycle 2026-04 --Limit 100]
```

```
# 密钥保护
用户：查一下配置里的 key

AI：Profile: default
  PublicKey: abcd****（已脱敏）
  PrivateKey: efgh****（已脱敏）
```

### Bad

```
# 未确认就执行写操作
用户：删掉那台测试机
AI：已删除。 ← 未展示影响范围和确认

# 暴露密钥
AI：PrivateKey 为 abcdefghijklmnop  ← 应脱敏

# US3 数据操作用 ucloud api
ucloud api --Action PutFile ...  ← 应提醒用户使用 us3cli
```

## 豁免

| 场景 | 条件 |
|------|------|
| 紧急故障恢复 | 用户明确声明紧急情况，可简化确认流程（仍需展示命令） |
