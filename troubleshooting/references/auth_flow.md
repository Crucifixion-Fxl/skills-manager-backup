# Troubleshooting 平台认证流程

## 概述

Troubleshooting 平台通过飞书 OAuth 授权获取 JWT Token（HS512，有效期 24 小时）。用户只需在浏览器中点击一次飞书授权即可完成认证。**零依赖**，仅使用 Node.js 内置模块，跨平台（macOS / Windows / Linux）。

## 认证流程

### 获取 Token

```bash
TROUBLESHOOTING_TOKEN=$(node <skill_dir>/references/get_token.mjs us)
# EU 区域：node <skill_dir>/references/get_token.mjs eu
```

`get_token.mjs` 内部流程：

1. **检查磁盘缓存**（`~/.troubleshooting-token-{region}`）→ 调 `/api/current` 验证 → 有效则直接返回
2. **缓存无效时**：启动本地 HTTP 服务器（`localhost:9876`）→ 打开飞书 OAuth 授权页 → 用户点击授权 → 飞书回调到 localhost 带上 code → 用 code 调微应用平台 `prepare` + `confirm` 接口换取 JWT → 缓存到磁盘

### 在 Skill 中的使用方式

1. **会话开始时获取一次**：如果 `TROUBLESHOOTING_TOKEN` 为空，运行 `get_token.mjs` 获取
2. **后续请求直接使用**：不要每次 API 调用都重新运行 `get_token.mjs`
3. **401 时重新获取**：清除变量 → 运行 `get_token.mjs` → 重试一次 → 仍 401 则报告失败

## 技术细节

- **飞书 OAuth 应用**：`cli_a818b4710778901c`（Micro App Platform/效能应用业务认证），回调地址 `http://localhost:9876/callback`
- **Token 换取**：飞书 code → `POST /api/auth/login/prepare` → `POST /api/auth/login/confirm` → JWT
- **Token 缓存**：`~/.troubleshooting-token-{region}`，避免重复认证
- **跨平台打开浏览器**：`/usr/bin/open`（macOS）/ `rundll32 url.dll`（Windows）/ `/usr/bin/xdg-open`（Linux）
- **四个环境共用同一个 Token**，从任意环境获取一次即可通用

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| 端口 9876 被占用 | 报告错误，提示用户关闭占用端口的程序 |
| 飞书授权超时（120s） | 脚本退出，提示用户重试 |
| prepare/confirm 接口失败 | 报告具体错误信息 |
| 401 重试后仍失败 | 报告认证失败，不进入循环 |

## 安全说明

- Token 存储在 shell 环境变量和本地缓存文件中，仅当前用户可访问
- 认证在用户自己的浏览器中完成，所有操作可见
- 本地 HTTP 服务器仅监听 127.0.0.1，不接受外部连接
- 脚本不存储或转发任何凭证
