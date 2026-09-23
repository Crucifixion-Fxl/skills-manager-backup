---
name: sentry-onboarding
description: Legacy/platform Sentry operations helper for A4x self-hosted Sentry. Use for explicitly requested instance/project migrations or consolidation, repairing sentry-onboard infrastructure, legacy projects, Vault DSN records, brand relay DSNs, or low-level Sentry API issues. For new application Sentry onboarding, use cicd-developer add-sentry instead.
---

# sentry-onboarding

## Description

这是低层运维 / 遗留兼容 skill，不是新应用接入入口。

新应用或现有服务要“接 Sentry”时，走 `cicd-developer` 的 `workflows/add-sentry.md`：它会生成 app-scoped ConfigMap、ServiceAccount、Job、ExternalSecret，并跑 validator。不要在本 skill 里手写这 4 个 manifest。

本 skill 只处理这些场景：

- `sentry-onboard` Job / Vault JWT / Sentry token / DSN 写入链路排障
- 历史 Project 或 legacy DSN path 修复
- 明确要求的跨实例项目迁移/实例合并；按 [项目与实例迁移](../cicd-developer/references/sentry/project-migration.md) 执行
- 品牌 relay DSN host 校验或补救
- 修复 `sentry-onboard` 工具本身时的 Sentry REST API 调用

## 实例与输入

| 环境 | 区域 | Sentry URL | 已知 team 候选 |
|------|------|------------|-----------|
| staging / prod 共享应用项目 | US | `https://sentry-us.addx.live` | `backend` / `frontend`，查询核实 |
| staging / prod 共享应用项目 | EU | `https://sentry-eu.addx.live` | `backend` / `frontend`，查询核实 |
| staging / prod 共享应用项目 | CN | `https://sentry-cn.addx.live` | `backend` / `team-zlin` / `team-mwang2`，查询核实 |

新接入的权威路由是 `cicd-developer/references/data/sentry-instances.yaml`，由 cluster.region 选择区域 prod；staging/prod 均使用 `<app>`，SDK environment 区分环境，ENV/Vault 路径保持 staging。backend/admin 用经验证的 `sentry-relay-{region}.addx.live` 私网入口；staging 移动端/浏览器必须先验证品牌公网 ingest 与实际终端可达，再显式填 DSN_HOST，未确认不能回退到私网 relay。工具仅在 prod 接受 BRAND 并改写，staging BRAND 必须省略。上表 team 只供发现，先查目标 org 及项目归属。旧 `sentry-staging-{region}.addx.live` 仅作历史迁移调查，不为新应用选择；其删除状态必须实时核验，不能从本表推断。

`sentry-onboard` Job 的 ConfigMap 至少要确认：

| 字段 | 说明 |
|------|------|
| `APP` | 应用与 Vault 路径身份，如 `payment-service`；不随目标项目改名 |
| `PROJECT_SLUG` | 可选 Sentry 项目名，空值默认 `APP`；需先核验镜像支持 |
| `DSN_HOST` | 可选已验证 ingest hostname，不含 scheme/port/path；C 端 prod 品牌 relay 优先，需先核验镜像支持 |
| `PLATFORM` | Sentry platform，需确认到框架级别，如 `java-spring-boot`、`python-fastapi`、`go` |
| `INSTANCE` | 目标 Sentry 实例，通常包含环境和区域 |
| `TEAM` | 目标 team slug |
| `APP_TYPE` | `backend` / `admin` / `mobile-app` / `web-frontend`，决定 DSN host 是否要品牌 relay |
| `ENV` | `staging` / `prod` 等 Vault env |
| `BRAND` | 仅 C 端可见 DSN 需要，如 `kiwibit` / `vicoo` / `vicohome` / `safemo` |
| `VAULT_*` | Vault 地址、路径 schema、目标 path/property |

镜像发布链路：`sentry-onboard` 统一构建到 SG Harbor `harbor-12571-sg-devops.addx.live/base/sentry-onboard:<sha>`，再通过 `base-images/internal-images.yaml` 扇出。各集群应使用本集群 `${HARBOR_REGISTRY}/base/sentry-onboard:<sha>`；不要再使用旧的 per-cluster `cicd/<env-region>/sentry-onboard` 镜像路径。

每个集群的 `VAULT_ADDR`、JWT mount、`SENTRY_DSN_CSS`、CN NAT 调度要求不要凭经验猜。能读到 `cicd-developer` 时，先查 `references/sentry/README.md`；否则从 `k8s/clusters/<cluster>/cicd/argocd/values-override.yaml`、ExternalSecret/ClusterSecretStore 和现有同类 Job 反查。

## Vault DSN Schema

| Schema | 路径 | property | 使用建议 |
|--------|------|----------|----------|
| legacy | `secret/{env}/app/{app}/sentry-dsn` | `dsn` | 既有应用保持不迁 |
| platform | `secret/{env}/sentry/application/{app}/project` | `dsn` | 新应用优先使用，前提是 app policy/ExternalSecret 已覆盖该路径 |

## DSN 域名规则

公司运营多品牌（kiwibit / vicoo / vicohome / safemo）。DSN 是否需要改写看 **是否会被 C 端看见**，不是只看 staging/prod。

| App 类型 | C 端可见 | Prod DSN host |
|---------|---------|---------------|
| 移动 App SDK / Web 前端 SDK | 是 | 必须用 `glitch-{region}.{brand}.{tld}` |
| 后端服务 / 内部管理后台 | 否 | 使用已验证的区域 relay `DSN_HOST` |

Staging 显式设置已验证的 `DSN_HOST`：服务端使用区域私网 relay，移动端/浏览器使用经验证的品牌公网 ingest。`BRAND` 在 staging 必须省略，非空会被工具拒绝；品牌入口通过 `DSN_HOST` 显式设置。Relay 改写只替换 host，保留 scheme、凭据和 project path。网页/API 可访问不等于 envelope 可接收，必须从实际应用网络验证。

## Rules

### 工作流

1. 先路由：用户只是要“给应用接 Sentry”时，STOP 并切到 `cicd-developer` `add-sentry`；明确迁移/合并、排障或修复 legacy/platform 链路时继续。
2. 收集 `APP`、有效 `PROJECT_SLUG`、必要的 `DSN_HOST`、`PLATFORM`、`APP_TYPE`、环境/区域、team、Vault schema/path；遵循已有任务授权，范围未明确时再确认。
3. 审查已有 `sentry-onboard` ConfigMap + Job，镜像必须是本集群 Harbor 的 `base/sentry-onboard:<sha>`，tag 不得是 `latest`。
4. 确认目标 Vault path 的写入权限已经存在；app owner/developer 权限来自 `gitlab-vault-sync` registry，不要手工改 app policy。
5. 通过 Job logs 验证 project 创建、DSN 改写、Vault 写入三步；再检查 ExternalSecret 是否读取同一个 Vault 实例和 path/property。

## 直接 API 的边界

只有在明确授权的实例迁移/合并、排障或修复 sentry-onboard 平台链路时才直接调用 Sentry REST API。Token 从已授权的本地凭据或目标 Vault 读取，不读取 `~/.claude/password`，不回显值。需要最新 API 参数时用 Context7 查询 Sentry 官方文档。

## 安全规则

1. 不在日志、MR 描述、最终回复中输出完整 Sentry token 或 DSN secret key。
2. Production project 创建和 Vault 写入前明确实例、team、Vault path；已有任务授权覆盖时不重复询问，缺少授权时先确认。
3. 创建前先查是否已有同名 Project；已有项目先核验归属再复用，不重复创建；同应用 staging 复用 production 项目，但须先核验 SDK environment 和生产告警排除 staging；项目已有而 staging Vault 无记录时仍需经核验的 operator 初始化，Job 不自动认领。

## Examples

### Bad

```
用户：帮我给 order-service 开 Sentry
AI：使用 sentry-onboarding 直接在 prod-us 创建 Project，再把 DSN 发到聊天里。
```

问题：新应用接入应该走 `cicd-developer add-sentry`，而且不能暴露 DSN secret。

### Good

```
用户：order-service 的 staging-us sentry-onboard Job vault login 403，帮我排查
AI：
1. 查目标集群的 VAULT_ADDR / VAULT_K8S_MOUNT / SENTRY_DSN_CSS，不凭经验猜
2. 检查 Job ServiceAccount projected token、Vault JWT role sentry-onboard、bound_claims 和 audience
3. 只输出 token/policy key 名，不回显 Sentry token 或完整 DSN
4. 修复后用 Job logs 和 ExternalSecret Ready 状态验证
```
