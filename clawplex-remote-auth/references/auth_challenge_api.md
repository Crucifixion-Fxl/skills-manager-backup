# Gateway Auth Challenge API

All authentication flows (API token, OAuth, QR code, OTP, VNC) use the same Gateway endpoints.

## POST /auth/challenge

Trigger a credential collection flow. Gateway sends the appropriate Feishu card/message to the user.

```json
{
  "auth_id": "auth_7f3a2b1c",
  "domain": "sentry.io",
  "type": "token",
  "message": "需要 sentry.io 的 API Token"
}
```

### Types

| type | Trigger | Feishu Action | Extra Fields |
|------|---------|---------------|--------------|
| `token` | 407 无凭证 (API Key/Token) | 发送凭证提交卡片 | — |
| `qr_code` | AI 检测到 QR 码 | 发送 QR 码截图 | `image_base64` (QR 截图) |
| `otp` | AI 检测到 MFA 页面 | 发送验证码输入卡片 | — |
| `vnc` | AI 遇到人机验证 / Social Login | 发送 VNC 连接链接 | `vnc_url` (Sandbox VNC 地址) |

> **注意：** OAuth/SSO 登录不作为独立的 challenge type。Agent 通过 AI 分析页面内容判断登录方式，优先选择飞书 SSO，无法自动化时 fallback 到 VNC。

### Examples

**Token (API场景):**
```json
{"auth_id": "auth_7f3a2b1c", "domain": "sentry.io", "type": "token"}
```

**QR Code:**
```json
{"auth_id": "auth_9d4e1a2f", "domain": "corp.feishu.cn", "type": "qr_code", "image_base64": "iVBOR..."}
```

**OTP:**
```json
{"auth_id": "auth_b2c8f3d1", "domain": "amazon.com", "type": "otp"}
```

**VNC:**
```json
{"auth_id": "auth_e5a7c9b4", "domain": "accounts.google.com", "type": "vnc", "vnc_url": "https://sandbox-vnc.corp.com/session/abc123"}
```

## GET /auth/status/{auth_id}

Poll the status of a challenge. Same endpoint and response format for all types.

```json
// Pending
{"status": "pending", "elapsed_seconds": 30, "message": "等待用户提交凭证"}

// Completed
{"status": "completed", "domain": "sentry.io"}

// Timeout
{"status": "timeout", "message": "凭证提交超时（5 分钟）"}

// Denied
{"status": "denied", "message": "用户拒绝了提交凭证"}
```

## Timeout Defaults

| Type | Timeout | Behavior |
|------|---------|----------|
| `token` | 5 min | Agent 提示用户超时 |
| `qr_code` | 5 min | 停止刷新 QR 码 |
| `otp` | 2 min | Agent 提示验证码超时 |
| `vnc` | 15 min | Agent 提示 VNC 操作超时 |
