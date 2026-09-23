---
name: sentry
description: Query Sentry errors, triage issues, and check release health via REST API. Use when debugging exceptions, investigating crash reports, triaging error issues, checking release stability, or any error monitoring and issue tracking task. Also triggers on mentions of Sentry, errors, exceptions, crashes, stack traces, or error rates.
---

# sentry

通过 Sentry REST API 查询错误、排查异常、追踪 Issue、监控 Release 健康状态。API 用法通过 Context7 MCP 查询，此处只记录公司特有的规则。

## Description

适用场景：线上错误排查、Issue triage、Release 健康检查、崩溃分析。

- **三区 prod Sentry 实例**承接 production 和 staging 共享应用项目（旧后缀项目保留历史），API 基础路径均为 `/api/0/`；旧 staging 实例仅作迁移历史调查，是否仍存在需实时核验
- **组织 slug**：`sentry`（各实例相同）
- **环境变量**：存储在 `skills/sentry/.env`（已 gitignore），使用前 `source skills/sentry/.env`

| 环境 | 区域 | URL 变量 | Token 变量 |
|------|------|----------|------------|
| prod / staging 共享应用项目 | US | `SENTRY_US_URL` (`https://sentry-us.addx.live`) | `SENTRY_US_AUTH_TOKEN` |
| prod / staging 共享应用项目 | CN | `SENTRY_CN_URL` (`https://sentry-cn.addx.live`) | `SENTRY_CN_AUTH_TOKEN` |
| prod / staging 共享应用项目 | EU | `SENTRY_EU_URL` (`https://sentry-eu.addx.live`) | `SENTRY_EU_AUTH_TOKEN` |

查询 staging 时先确认实际迁移映射；新默认复用原应用项目 `<app>`，旧 `<app>-staging` 保留迁移前历史，查询项目实际环境列表后再筛选，例如 `staging`、`staging-us`、`staging-cn`；不能假定 SDK 环境名等于 Vault ENV，查询共享项目必须同时限定目标 staging environment，避免把 production 数据当成 staging。旧 `SENTRY_STAGING_*` 变量只用于明确的历史调查，不作为默认 fallback。事件 ingest 使用适合 SDK 运行网络的 relay/品牌域名，管理 API 仍使用上表主域。

认证：`-H "Authorization: Bearer $SENTRY_{region}_AUTH_TOKEN"`

> 当前 Organization Token 默认只有 Release 相关权限。如需查询 Issue/Event，需在 Developer Settings 中创建 Internal Integration，勾选 `Issue & Event: Read`、`Project: Read`、`Organization: Read`。

## Rules

### 常见项目示例（非完整清单）

| 项目 slug | 产品 | 平台 | 说明 |
|-----------|------|------|------|
| `vh-android` | Vicoo Home | Android | 主力消费者 App（发布最频繁） |
| `vh-ios` | Vicoo Home | iOS | 主力消费者 App |
| `iot-service` | IoT 后端 | Java | 后端服务（与 iot-consumer 共享 Release） |
| `iot-consumer` | IoT 后端 | Java | 后端消费者服务 |
| `kb-android` | KiwiBit | Android | 子品牌 App |
| `kb-ios` | KiwiBit | iOS | 子品牌 App |
| `projectf` | SafeMo | iOS | 安防 App |

### Release 版本命名规范

**三种格式**，按平台区分：

| 平台 | 格式 | 示例 |
|------|------|------|
| **Android** | `{major}.{minor}.{patch}({commit_hash})` | `2.85.0(b4f16c)` |
| **iOS** | `{bundle_id}@{version}+{build_number}` | `addx.ai.vicoo@3.91.0+30713` |
| **后端** | `{YYYYMMDD}@{COMMIT_HASH}` | `20260212@3A5AE26` |

**iOS Bundle ID 对照**：

| Bundle ID | 产品 |
|-----------|------|
| `addx.ai.vicoo` | Vicoo Home |
| `com.ai.addx.guard` | Vicoo Guard |
| `com.kb.kiwibit` | KiwiBit |
| `com.safemo.app` / `com.ai.safemo` | SafeMo |

### Release 发布频率

- **vh-android**：每天多次构建（CI 自动上报）
- **vh-ios**：每周数次
- **iot-service / iot-consumer**：约每日部署，版本号含日期（`YYYYMMDD@HASH`），两个项目共享同一 Release
- **kb-***：较低频率

### 操作红线

1. **先查后改**：写操作（resolve / ignore / assign）必须先查看堆栈和影响范围
2. **PII 保护**：错误上下文可能含用户数据，禁止将原始堆栈外传或暴露用户信息

### 常见工作流

1. **排查线上错误**：查询项目未解决 Issue → 按时间排序 → 查看最新事件堆栈 → 定位代码行 → 给出修复建议
2. **部署后健康检查**：查询最新 Release → 检查新增 Issue → 确认 Crash Free Rate → 持续观察 30min
3. **批量 Triage**：按项目 + 环境筛选 → 逐条查看堆栈 → resolve / ignore / assign
4. **Release 追踪**：`/api/0/organizations/sentry/releases/?per_page=20&sort=date` → 检查最新发布

## Examples

### Bad

```
# 未排查就直接 resolve
用户：帮我处理一下 Sentry 上的报错
AI：已帮你 resolve 了所有未解决 Issue。  ← 未查看堆栈和影响范围

# 泄露 PII
AI：堆栈显示用户邮箱 user@example.com 触发了异常...  ← 不应暴露用户信息
```

### Good

```
# 部署后检查
用户：后端刚发了新版本，帮我看看 Sentry 有没有新错误
AI：查询最新 Release...
  1. 查到 20260303@A1B2C3D，关联 iot-service 和 iot-consumer
  2. 查看该 Release 关联的 Issue...
  3. 目前无新 Issue，建议继续观察 30min

# 客户端崩溃排查
用户：vh-android 最新版 2.85.0 有用户反馈闪退
AI：查询 vh-android 项目 Release 2.85.0 相关 Issue...
  1. 找到最新构建 2.85.0(b4f16c)
  2. 查看未解决 Issue，按影响用户数排序
  3. 查看 top Issue 堆栈 → 定位代码
```
