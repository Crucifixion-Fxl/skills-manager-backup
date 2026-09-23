---
name: feishu-auth
description: 为明确依赖效能应用或内部业务平台 OAuth 的 Skill 提供 @a4x/feishu-auth-cli 认证。仅在 Troubleshooting、Micro App Platform、Marketing CMS 等业务 Skill 显式声明依赖时使用；普通飞书消息和资源操作使用 lark-cli 内置 lark-shared 指南。
---

# 效能应用业务 OAuth

## Description

本 Skill 只管理内部业务平台使用的 `@a4x/feishu-auth-cli` token。它不是普通飞书
消息、文档、Wiki、Base、任务、日历或通讯录的认证入口，也不是 Codex Lark MCP 的
回退方案。普通 OAuth 恢复按 `feishu-channel-rules` 完成门禁后读取 CLI 内置 `lark-shared`。

普通飞书用户资源应读取 CLI 内置 `lark-shared`，并使用：

```bash
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" --profile <approved-profile> <domain> <command> --as user
```

## Rules

1. 只有调用方 Skill 明确声明依赖效能应用/内部业务平台认证时才使用本 Skill。
2. 使用前确认调用方的目标是业务平台 API，而不是飞书 OpenAPI 用户资源。
3. token 仅传给调用方明确指定的业务 API；不得传给普通飞书命令或旧 Codex MCP。
4. 不输出 access token、refresh token 或 npm registry 凭据。
5. 认证失败时报告当前业务平台阻塞，不得改用普通 `lark-cli` user profile 猜测性重试。

## Commands

仅允许内部 registry 的已批准版本：

```bash
FEISHU_AUTH_REGISTRY="https://gitlab.addx.ai/api/v4/projects/1021/packages/npm/"
test "$(npm config get @a4x:registry)" = "$FEISHU_AUTH_REGISTRY" || exit 1
npx --yes @a4x/feishu-auth-cli@1.2.1 --version

npx --yes @a4x/feishu-auth-cli@1.2.1 status
npx --yes @a4x/feishu-auth-cli@1.2.1 login
npx --yes @a4x/feishu-auth-cli@1.2.1 refresh
```

版本输出必须精确为 `1.2.1`（允许 CLI 固定前缀）；registry 缺失、认证失败或版本不符时
立即停止。不得改用公共 npm registry、未固定版本或本地缓存中的其他版本。

业务调用方需要 token 时，只能把固定版本 CLI 的 stdout 直接捕获到进程内变量；不得先把
token 打印到终端、日志或文件。调用完成后清除变量。

仅当业务 Skill 明确要求覆盖 scope 时使用细粒度 scope：

```bash
npx --yes @a4x/feishu-auth-cli@1.2.1 login --scope "contact:user.email:readonly offline_access"
```

不要使用 `bitable`、`contact`、`message`、`wiki` 这类简写 scope。不要把 token
写入命令输出；本地持久化位置由 CLI 管理。

## Known Consumers

- `troubleshooting`：通过 Micro App Platform/效能应用 OAuth 换取平台 JWT
- `micro-app-auth-integration`：微应用及独立 Web 应用的统一登录、鉴权与审批边界
- `marketing-cms`：内部 CMS 业务 API 认证

普通飞书文档、消息及媒体读取能力不依赖本 Skill。

## Examples

### Good

```text
Marketing CMS Skill 明确要求内部业务 token，因此调用 feishu-auth。
```

### Bad

```text
发送飞书消息失败后，改用 feishu-auth token 或旧 lark MCP 重试。
```
