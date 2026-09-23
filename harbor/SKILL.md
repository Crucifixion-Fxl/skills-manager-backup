---
name: harbor
description: 通过 Harbor API 管理镜像仓库项目和 Robot Account。为指定项目创建专属推拉镜像的机器人账号，获取凭证用于 CI/CD Pipeline。
---

# harbor

通过 Harbor REST API 管理公司内部 Harbor 镜像仓库。核心场景：为**业务/CI 项目仓库**创建项目级 Robot Account，获取推拉镜像的凭证。开源/基础镜像同步到各集群 `base/` 项目时优先走 `base-images`，不要手工在每个 cluster Harbor 创建账号或推镜像。

**适用场景：**

- 为新服务申请 Harbor 项目路径的 Robot Account（CI/CD 推拉镜像）
- 查看项目下已有的 Robot Account
- 查看项目的镜像仓库和 Tag 列表
- 创建新的 Harbor 项目

## Harbor 实例

| 类型 | 示例域名 | 说明 |
|------|----------|------|
| SG 源 Harbor | `harbor-12571-sg-devops.addx.live` | base/internal image fanout 源头；provider/sentry-onboard 等平台镜像先推这里 |
| 每集群 Harbor | `harbor-00249-us-tech.addx.live`、`harbor-39070-us-staging.addx.live`、`harbor-80144-cn-staging.addx.live`、`harbor-a4xp-us-prod.addx.live` | 当前主路径。具体列表以 `DEV/base-images/.gitlab-ci.yml` 和 `k8s/clusters/*/harbor*/values*` 为准 |
| 历史共享 Harbor | `harbor-us-internal.addx.live`、`registry-harbor-cn.addx.live`、`registry-harbor-sg.addx.live` | 兼容旧镜像和旧 pull secret，不要作为新项目默认目标 |
| TKE Harbor | `harbor-cn.addx.live` | 腾讯云 TKE 使用 |

> **注意：** 部分域名为内网域名，需确保网络可达（VPN、集群内、或对应网络环境）。

## 认证

所有 API 请求使用 Basic Auth，需要 Harbor 管理员账号。

```bash
# 环境变量（运行前设置）
export HARBOR_USERNAME="admin"
export HARBOR_PASSWORD="<admin-password>"
```

如果用户未提供凭证，引导用户设置以上环境变量。

## 执行流程

### Step 1: 确认目标 Harbor 实例

根据用户需求确认使用哪个 Harbor 实例：

| 用户描述 | 目标实例 |
|----------|----------|
| base 镜像 / provider / sentry-onboard 扇出 | 不手工创建 Robot，走 `DEV/base-images` |
| 某个集群内项目需要 Robot | 使用该集群 Harbor，如 `harbor-00249-us-tech.addx.live` |
| 历史 US/EU 项目仍在共享 Harbor | `harbor-us-internal.addx.live` |
| 历史 CN AWS 项目 | `registry-harbor-cn.addx.live` |
| TKE 项目 | `harbor-cn.addx.live` |

设置基础 URL：

```bash
HARBOR_HOST="harbor-00249-us-tech.addx.live"  # 根据实际情况替换
HARBOR_URL="https://${HARBOR_HOST}/api/v2.0"
```

### Step 2: 验证连接和权限

```bash
curl -sk -u "${HARBOR_USERNAME}:${HARBOR_PASSWORD}" \
  "${HARBOR_URL}/systeminfo" | python3 -m json.tool
```

期望返回 Harbor 版本信息。如果失败，检查网络连通性和凭证。

### Step 3: 确认或创建项目

#### 查看项目是否存在

```bash
curl -sk -u "${HARBOR_USERNAME}:${HARBOR_PASSWORD}" \
  "${HARBOR_URL}/projects?name=<project-name>" | python3 -m json.tool
```

#### 如果项目不存在，创建项目

```bash
curl -sk -u "${HARBOR_USERNAME}:${HARBOR_PASSWORD}" \
  -X POST "${HARBOR_URL}/projects" \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "<project-name>",
    "metadata": {
      "public": "false"
    },
    "storage_limit": -1
  }'
```

- `public: false` — 私有项目，需认证才能拉取
- `storage_limit: -1` — 不限制存储

### Step 4: 创建 Robot Account

为项目创建专属 Robot Account，用于 CI/CD 推拉镜像。

```bash
curl -sk -u "${HARBOR_USERNAME}:${HARBOR_PASSWORD}" \
  -X POST "${HARBOR_URL}/robots" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "<robot-name>",
    "description": "<用途描述，如: CI/CD pipeline for service-xxx>",
    "duration": -1,
    "level": "project",
    "permissions": [
      {
        "kind": "project",
        "namespace": "<project-name>",
        "access": [
          {"resource": "repository", "action": "push"},
          {"resource": "repository", "action": "pull"},
          {"resource": "tag", "action": "create"}
        ]
      }
    ]
  }'
```

**参数说明：**

| 参数 | 说明 | 建议值 |
|------|------|--------|
| `name` | Robot 名称，最终账号为 `robot$<project-name>+<name>` | 用服务名，如 `ci-deployer` |
| `duration` | 有效期（天），`-1` 为永不过期 | `-1` |
| `level` | 作用范围 | `project` |
| `permissions.access` | 权限列表 | push + pull + tag create（push 必须搭配 pull，因为推镜像时需要 HEAD 检查 manifest） |

**成功响应示例：**

```json
{
  "id": 42,
  "name": "robot$my-project+ci-deployer",
  "secret": "xxxxxxxxxxxxxxxxxxxx",
  "creation_time": "2026-03-05T10:00:00.000Z",
  "expires_at": -1
}
```

> **重要：`secret` 只在创建时返回一次，必须立即记录！** 后续无法再次查看。

### Step 5: 输出凭证

将 Robot Account 凭证整理输出给用户，**必须包含 HARBOR_REGISTRY**。只有用户明确需要公网/hosts 兜底时，才额外确认并输出对应 IP；不要从旧文档猜 IP。

```
HARBOR_REGISTRY:  <HARBOR_HOST>
项目路径:         <HARBOR_HOST>/<project-name>/<image-name>:<tag>
Robot 账号:       robot$<project-name>+<robot-name>
Robot 密钥:       <secret>

Docker 登录命令:
  docker login <HARBOR_HOST> -u 'robot$<project-name>+<robot-name>' -p '<secret>'

镜像推送示例:
  docker tag <local-image> <HARBOR_HOST>/<project-name>/<image-name>:<tag>
  docker push <HARBOR_HOST>/<project-name>/<image-name>:<tag>
```

> 不要为了“方便”把 Harbor 暴露面或 DNS 写法临时改公网；网络不可达时先确认 runner/集群网络和现有 Harbor ingress/DNS。

### Step 6 (可选): 验证凭证

```bash
curl -sk -u "robot\$<project-name>+<robot-name>:<secret>" \
  "${HARBOR_URL}/projects/<project-name>/repositories" | python3 -m json.tool
```

## 其他常用操作

### 查看项目下的 Robot Account 列表

```bash
curl -sk -u "${HARBOR_USERNAME}:${HARBOR_PASSWORD}" \
  "${HARBOR_URL}/projects/<project-name>/robots" | python3 -c "
import sys,json
for r in json.load(sys.stdin):
    print(f'{r[\"name\"]:40s} expires={r.get(\"expires_at\",\"N/A\")}  disabled={r.get(\"disable\",False)}')
"
```

### 查看项目的镜像仓库

```bash
curl -sk -u "${HARBOR_USERNAME}:${HARBOR_PASSWORD}" \
  "${HARBOR_URL}/projects/<project-name>/repositories?page_size=50" | python3 -c "
import sys,json
for r in json.load(sys.stdin):
    print(r['name'])
"
```

### 查看镜像 Tag 列表

```bash
curl -sk -u "${HARBOR_USERNAME}:${HARBOR_PASSWORD}" \
  "${HARBOR_URL}/projects/<project-name>/repositories/<repo-name>/artifacts?page_size=20" | python3 -c "
import sys,json
for a in json.load(sys.stdin):
    tags = ','.join(t['name'] for t in a.get('tags') or [])
    print(f'{tags:40s} size={a[\"size\"]/(1024*1024):.1f}MB  push_time={a[\"push_time\"][:19]}')
"
```

### 删除 Robot Account

```bash
curl -sk -u "${HARBOR_USERNAME}:${HARBOR_PASSWORD}" \
  -X DELETE "${HARBOR_URL}/robots/<robot-id>"
```

## 安全规则

1. **Robot Account 的 secret 只在创建时返回一次**，必须立即告知用户保存
2. **使用 project 级别 Robot Account**，不要创建 system 级别（权限过大）
3. **凭证不能硬编码到代码中**，应存入 Vault 或 CI/CD 变量
4. **私有项目**：默认创建 private 项目，除非用户明确要求 public
5. **命名规范**：Robot 名称应体现用途，如 `ci-deployer`、`image-puller`
6. **CI/CD 用途的 Robot Account 必须同时给 push + pull 权限** — 推镜像时 Docker/Kaniko 需要先 HEAD 检查 manifest 是否已存在（属于 pull 操作），缺少 pull 权限会导致 401 Unauthorized。仅当用户明确只需要拉取时才只给 pull 权限

## 示例

### Bad

#### 1. 创建 system 级别 Robot Account

```
level: "system"  # 权限过大，可以访问所有项目
```

**问题**：system 级别 Robot 可以操作所有项目，违反最小权限原则。

#### 2. CI/CD 推镜像账号缺少 pull 权限

```json
"access": [
  {"resource": "repository", "action": "push"},
  {"resource": "tag", "action": "create"}
]
```

**问题**：推镜像时 Docker/Kaniko 需要先 HEAD 检查 manifest（属于 pull 操作），缺少 pull 权限会导致 `401 Unauthorized`。CI/CD push 账号必须同时具备 push + pull。

#### 3. 忘记记录 secret

```
创建完 Robot Account 后没有输出 secret 就继续下一步
```

**问题**：secret 只返回一次，丢失后只能删除重建。

### Good

#### 1. 为新服务创建完整凭证

```
用户：帮我在 US Harbor 上为 payment-service 创建推镜像的账号

AI：
1. 确认目标是历史共享 Harbor 还是当前集群 Harbor（例如 harbor-00249-us-tech.addx.live）
2. 检查 payment-service 项目是否存在（不存在则创建）
3. 创建 Robot Account: robot$payment-service+ci-deployer
4. 立即输出完整凭证（账号、密钥、docker login 命令、推送示例）
5. 提醒用户将密钥存入 Vault 或 CI 变量
```

#### 2. 为已有项目添加只读拉取账号

```
用户：我需要一个只能拉取 common-libs 镜像的账号

AI：
1. 创建 Robot Account，access 只配置 pull（不含 push）
2. 输出凭证并说明此账号只有拉取权限
```
