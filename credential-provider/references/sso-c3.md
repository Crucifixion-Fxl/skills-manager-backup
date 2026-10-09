# sso-c3.md — C3 平台 SSO token 扩展

> **上游 SSOT**：[sso-platforms.md](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/credential-provider/sso-platforms.md)（121 行）。本 cheat sheet 只覆盖"AI 帮用户配置 C3 token 注入"的快通道。

## 1. 何时用

需要 Vaultwarden 之外的实时 token 时——这些平台 token 不能存 vault（短时效 / 用户绑定）：

| 平台 | env var | 来源 |
|---|---|---|
| Troubleshooting | `TROUBLESHOOTING_TOKEN` | Casdoor (OAuth2) |
| CS Workspace | `CS_WORKSPACE_TOKEN` | Casdoor (OAuth2) |
| Dagster | `DAGSTER_TOKEN` | Casdoor + OAuth2 Proxy |
| DAPP | `DAPP_TOKEN` | 飞书 App OAuth2 |

## 2. 必备环境变量

以下配置键由受控 secret manager 或用户本机受保护的 shell profile 注入。不要在命令行、聊天、文档或日志中粘贴真实值：

| 配置键 | 来源 |
|---|---|
| `CP_FEISHU_APP_ID` / `CP_FEISHU_APP_SECRET` | 飞书应用配置（受控 secret source） |
| `CP_CASDOOR_BASE_URL` | Casdoor 环境配置 |
| `CP_C3_PLATFORMS` | 受控平台配置 |

每个启用平台再加一对 client_id / client_secret，命名约定：`CP_<TOKEN_NAME>_CASDOOR_CLIENT_{ID,SECRET}`；具体值只从受控 secret source 读取。
PowerShell 形态把 `export X=Y` 改成 `$env:X = "Y"`，详见 [quickstart §4](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/user-guide/quickstart.md#4-sso-c3-平台可选)。

## 3. 与 A 类凭证（Vaultwarden）并行不互斥

`env` 一次性输出 **A + B + C** 三类 export 语句：

- A 类：Vaultwarden Hidden custom field
- B 类：Vaultwarden 标准字段
- C 类：本文 SSO 平台 token

C3 配置缺失 → SSO 整体跳过，**不影响** A/B 注入。`--no-sso-token` 显式跳 C3（外网/弱网常用）。

## 4. 第一次跑

```bash
# 跑这条会弹浏览器走飞书 OAuth；refresh token 写入 ~/.credential-provider/feishu-refresh（0600，30 天）
eval $(credential-provider env)
```

之后 30 天内每次 `env` 都走 refresh token 快速路径（~500ms，不弹浏览器）；过期回退到完整 OAuth。

## 5. 故障速查

| 症状 | 处理 |
|---|---|
| C3 token 注入后某个为空 | 逐项检查变量是否非空，只输出变量名和 `is set` / `is not set`；禁止使用 `env \| grep CP_` 打印凭证值 |
| stderr `SSO config absent...` | `CP_FEISHU_APP_ID` / `CP_FEISHU_APP_SECRET` / `CP_CASDOOR_BASE_URL` / `CP_C3_PLATFORMS` 任意缺失 |
| stderr `SSO <PLATFORM>: ...` warning | 该平台 client_id/secret 缺失或错；A/B 仍能正常注入 |
| `feishu-refresh` 损坏 | `credential-provider lock` 清掉 session + feishu-refresh，下次 `env` 重新走一次飞书 OAuth |
| ClawPlex Sandbox / 无浏览器环境 | 不导出、落盘或跨边界传输完整 secret 环境；只在目标 sandbox 内通过受控身份/凭证 broker 按最小权限按需注入 |

只检查共通配置是否存在，不输出任何值：

```bash
for var in CP_FEISHU_APP_ID CP_FEISHU_APP_SECRET CP_CASDOOR_BASE_URL CP_C3_PLATFORMS; do
  if [[ -n "${!var:-}" ]]; then echo "$var is set"; else echo "$var is not set"; fi
done
```

> 禁止使用 `env --export-file` 生成包含真实凭证的文件，也禁止把完整 secret 环境通过文件、stdout 或未定义的通道传入 sandbox。若目标 sandbox 需要凭证，使用已批准的身份/凭证 broker 在 sandbox 内完成单个变量的最小权限注入，并在进程退出时清理。

## 6. `--no-sso-token`

```bash
eval $(credential-provider env --no-sso-token)
```

**何时用**：

- 外网/弱网，Casdoor 不可达
- CI runner（用服务账号 A 类凭证，避免绑个人 SSO）
- 调试 A/B 类问题，排除 C3 干扰

## 7. 不写的内容（去上游看）

- OAuth loopback 时序 + 不变量 → 上游 §3
- refresh token 30 天快速路径细节 → 上游 §3.3
- 无浏览器环境的三种应对 → 上游 §3.4
- 为什么不用 Device Code Flow → 上游 §3.5
- 降级策略详细矩阵 → 上游 §4
