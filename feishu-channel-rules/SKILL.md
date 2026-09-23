---
name: feishu-channel-rules
description: |
  飞书工具、应用和身份的统一路由规则。发送或读取飞书消息，读写文档/Wiki、
  多维表格、任务、日历、通讯录等普通个人飞书资源时使用；也用于判断何时才应
  切换到 Troubleshooting、Micro App Platform、效能应用或其他应用身份。
  lark-cli 登录过期/未登录的远程恢复（device 授权），以及外部系统账号
  （如 GitLab 用户）到 Lark 收件人的解析，也由本规则定义。
alwaysActive: true
---

# 飞书应用与身份路由

## Description

本 Skill 是飞书能力的路由 SSOT。具体命令和参数由 `lark-cli` 自带、与版本匹配的
Skill 决定；这里仅决定使用哪个应用、profile 和身份，并保留简洁的飞书输出规范。

## Rules

### 1. 默认应用和身份

每个任务首次执行任何联网、认证或用户数据相关的 CLI 操作前，必须先完整执行
[`references/approved-lark-cli.md`](references/approved-lark-cli.md) 的可执行文件与指令基线
门禁。除门禁指定的本地校验命令外，不得先运行 `whoami`、`--help`、读取 token 或业务命令。
后续命令只能用本地策略批准的 Node 二进制绝对路径执行批准的 CLI 入口绝对路径，不得
再次通过 PATH 或脚本的 `/usr/bin/env` 解析 runtime。

普通个人飞书操作显式绑定本地策略批准的 profile：

```bash
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" --profile <approved-profile> <domain> <command> --as user
```

- profile 与应用：由本机 `AGENTS.md` 或等价的用户级策略批准；不得只依赖可变的 active profile
- 身份：显式传 `--as user`
- 适用范围：IM 消息、文档/Wiki、Base、任务、日历、通讯录、云盘、邮件等用户资源
- 每次写操作前立即运行
  `"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" --profile <approved-profile> whoami`；返回的 profile、
  App ID、应用名、用户和 user token 状态必须与本地批准值精确匹配，否则停止
- 身份核验与随后的写命令必须使用同一个显式 `--profile`，中间不得切换 profile

本机配置还应把 `default-as`、`strict-mode` 设为 `user`；命令仍显式传 profile 和
`--as user`。不得因为另一个飞书 MCP 或应用也能调用相同 OpenAPI，就让模型自行选择。
Agent 不得自行执行 `profile use` 或更改 strict mode。

### 2. 允许使用其他应用的场景

只有以下情况才切换应用：

| 场景 | 路由 |
|---|---|
| Troubleshooting、Micro App Platform、效能应用后台的业务接口 | 使用对应业务 Skill 明确声明的应用/凭据 |
| Webhook、Bot 回包、Bot 自有资源或用户明确要求应用身份 | 使用该应用的 bot identity |
| 无人直接发起的 Agent 自动通知（定时任务、CI、自动流转产生的指派通知） | 同一已批准 profile 的应用 bot identity（`--as bot`，见第 3 节） |
| 用户明确指定另一个飞书应用 | 按用户指定执行 |

任务只是“发飞书”“读文档”“改任务”“查日历”时，不属于上述例外。

CLI 的 user identity 报权限错误时，先按
`"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" skills read lark-shared` 恢复用户授权；
不得静默降级到 Codex `mcp_servers.lark`、效能应用或 bot identity。资源本身无权限时，
停止并报告阻塞。

### 3. 发送消息

普通私聊固定使用：

```bash
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" \
  --profile <approved-profile> im +messages-send --as user \
  --user-id <ou_xxx> --text '<content>' \
  --idempotency-key <stable-key>
```

普通群聊将收件参数改为 `--chat-id <oc_xxx>`；`--user-id` 与 `--chat-id` 不得同时传。

- 发送前将私聊收件人唯一匹配到 open_id，或将群聊唯一匹配到 chat_id；有歧义就停止
- 使用稳定幂等键，避免重试造成重复消息
- `--dry-run` 只验证请求，不代表已发送
- 分开报告 API 发送确认和内容回读；回读受 scope 限制时不得声称已回读
- `reply-feishu` 只决定卡片/文本格式，不决定发送应用或身份

通知类发送（GitLab 指派通知等）的身份**不按上款固定 `--as user`**，按触发来源选择：

- **人触发的操作**（本次发送对应的动作由人直接发起）：以操作者或其授权的
  登录身份 `--as user` 发送，消息中署实际操作者
- **无人直接发起的 Agent 自动流转**（定时任务、CI、Agent 自主推进产生的
  指派）：将同一发送命令的 `--as` 改为 `bot`，用该 profile 绑定应用的
  bot identity 发送，并在消息内容署明 Agent 代发
- bot DM 不可达（应用可见范围、未建会话、scope 缺失）时如实报告未通知，
  不得改以个人身份冒名补发；两种身份都沿用本节的幂等键与回读规则

### 4. 其他普通能力

门禁核验通过后再进入具体能力：

```bash
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" skills read lark-shared
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" skills read lark-im
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" skills read lark-doc
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" skills read lark-base
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" skills read lark-task
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" skills read lark-calendar
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" skills read lark-wiki
```

所有普通用户资源命令都保留 `--as user` 和显式 `--profile <approved-profile>`。

### 5. 飞书输出格式

- 短句、低仪式感，能一两句话说清就不堆 bullet
- 只在结构化数据确实更清楚时使用表格或卡片
- 不确定飞书 Markdown 时参考 `references/markdown-syntax.md`

### 6. 登录与授权恢复（可远程完成）

`whoami` / `auth status` 显示 user token 失效、未登录或缺 scope 时，按本节恢复；
这是第 2 节「恢复用户授权」的可执行路径。`auth login` 走 OAuth device 授权
（RFC 8628）：CLI 向服务端轮询，用户在自己任一设备的浏览器打开 verification_url
或扫二维码完成授权。用户**不需要**访问 Agent 主机——没有 localhost callback 依赖，
也不需要 LAN callback 或 SSH 端口转发。

```bash
# 1. 发起并立即返回 device_code 与 verification_url
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" --profile <approved-profile> auth login <scope 参数> --no-wait --json
# 2. 把 verification_url 转成二维码（内建 skill 要求生成并展示）
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" auth qrcode <verification_url> --output <qr.png>
# 3. 用户确认已授权后，恢复轮询完成登录
"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" --profile <approved-profile> auth login --device-code <device_code>
```

- scope 参数必须显式（如 `--recommend`，或按 `skills read lark-shared` 选定
  `--domain` / `--scope`）；非交互环境不得裸跑 `auth login` 依赖 TUI 选择
- `verification_url` 按不可修改的 opaque 字符串原样转交：不得 URL-encode/decode、
  加空格或标点
- 把 URL（及二维码）作为最终回复交给用户后**结束当前轮**，不得同轮接着执行
  `--device-code`；用户完成授权并回复后再执行第 3 步
- 每次重新发起 `auth login` 都会使上一个 device code 与 verification_url 作废：
  重试必须整轮重来，不得复用缓存的 code 或 URL。轮询最长阻塞约 10 分钟，
  不要用短超时反复重启
- 恢复成功后重新执行第 1 节的 `whoami` 门禁核对 profile、身份与 token 状态
- 恢复失败或用户不在场时如实报告阻塞；不得改用其他应用、bot 身份或 MCP 旁路

### 7. 外部系统账号 → Lark 收件人解析

需要把外部系统身份（如 GitLab username）通知到具体的人时，先唯一解析成 Lark
收件人再发送：

1. 默认映射规则：GitLab username 拼上企业邮箱域 `@a4x.io` 即飞书企业邮箱
   （如 `qlv` → `qlv@a4x.io`），再经
   `contact +search-user --query "<email>"` 唯一反查 open_id（需已授权
   contact 读 scope，先用 `auth status` 核验；同名细化与批量解析按
   `skills read lark-contact`）
2. GitLab 用户名与邮箱前缀不一致的人，用本机策略批准的映射表覆盖；映射表的
   存在、来源与路径由本机 `AGENTS.md` 或等价用户级策略批准
3. 零命中、多命中或没有可信数据源时停止：不发、不猜、不硬编码 open_id，
   在交付报告里如实写「未通知 + 原因」

解析成功后按第 3 节发送；幂等键按（对象 × 收件人 × 指派变化）构造——含前任
assignee / reviewer，使 A→B→A 改回同一人仍是新事件；同一变化重试复用原键，
不重复发送。部分收件人解析失败不阻塞其余收件人，按实际成功与失败分别报告。

## Examples

### Bad

```text
发送失败后自动改用另一个 MCP 或 bot 应用重试。
```

### Good

```text
先确认批准的 profile/user 身份；普通飞书操作固定走 lark-cli。
只有任务明确属于效能应用业务域时，才使用对应业务应用。
```
