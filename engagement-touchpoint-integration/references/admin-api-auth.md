# Engagement Admin API 鉴权与自动化

## 结论

Engagement Admin 现在提供与 Troubleshooting 相同风格的默认浏览器
OAuth handoff，但两者的 Token 仍由各自系统签发，不能混用：

| 系统 | OAuth 后的产物 | API 如何认证 |
|---|---|---|
| Troubleshooting | 微应用平台签发的 JWT | `Authorization: Bearer <token>` |
| Engagement Admin 网页 | Admin 自己签发的 `admin_session` | httpOnly Cookie |
| Engagement Admin helper | Admin 自己签发的 CLI Token | `Authorization: Bearer <token>` |

因此不得把 `~/.troubleshooting-token-*`、普通飞书 access token 或微应用平台 JWT 当成 Admin Bearer token。Admin CLI Token 和网页 Session 虽由同一服务签发，但有独立的 token type/purpose，不能互换。

## Admin 实例与规则环境

`admin_api.mjs` 的第一个参数选择的是 **Admin 部署实例**，不是 rules package 的目标环境：

| helper 参数 | Admin 主机 | 用途 |
|---|---|---|
| `prod` | `https://engagement-admin.addx.live` | 日常触点创建、修改、查重和 PublishOrder；默认使用 |
| `staging` | `https://engagement-admin-staging.addx.live` | 仅验证 Admin 应用自身的预发版本；必须由用户明确指定 |

从 production Admin 调用 `POST /api/publish-orders` 后，首先生成 staging rules package。不要因为业务要求 staging-first 就改用 `admin_api.mjs staging ...`。推进 production rules 是后续独立操作，需要单独授权。

## Helper

使用 `scripts/admin_api.mjs`。它在 `127.0.0.1` 随机端口启动临时回调，使用系统默认浏览器访问对应 Admin 实例的 CLI 授权入口。浏览器会复用已有 Admin 登录状态；若 Session 已过期，再进入正常飞书 OAuth。Admin 使用短时效授权码 + PKCE 换取 8 小时 CLI Bearer Token，helper 随后直接调用 API。Cookie、OAuth code 和 Token 都不得打印。

```bash
SKILL_DIR=/absolute/path/to/engagement-touchpoint-integration

# 只读查重；首次运行会交给默认浏览器完成一次飞书授权
node "$SKILL_DIR/scripts/admin_api.mjs" \
  prod GET '/api/touchpoints?q=vh_home_banner'

# 写请求使用文件，便于用户在提交前审阅 payload
node "$SKILL_DIR/scripts/admin_api.mjs" \
  prod POST '/api/touchpoints/vh_home_banner/experience-variants' \
  @/absolute/path/to/payload.json \
  --confirm-write='prod:POST:/api/touchpoints/vh_home_banner/experience-variants'
```

CLI Token 缓存在 `~/.engagement-admin-api/<environment>/cli-token.json`，目录权限 `0700`、文件权限 `0600`。Token 过期或服务端拒绝时 helper 再次交给默认浏览器授权。不要读取、打印、复制或提交该文件。旧版本留下的专用浏览器 profile 不再使用，可由用户自行删除。

## 写操作门禁

执行 `POST`、`PATCH`、`DELETE` 前必须：

1. 先 `GET` 当前对象并查重。
2. 向用户展示目标环境、方法、路径和完整 JSON payload。
3. 获得用户对该次写操作的明确授权。
4. 传入与环境、方法和规范化路径完全一致的 `--confirm-write=...`；helper 会在发送前把目标和 payload 输出到 stderr。
5. 发布单相关写操作还必须另传 `--confirm-publish`，并取得单独授权。
6. 写入后再次 `GET`，逐字段核对响应与目标配置。
7. 显式带 `x-engagement-admin-operator`；helper 从 CLI 授权响应自动取已验证的企业邮箱、openId 或姓名。

对 Admin staging 实例执行写请求时，还必须显式传 `--confirm-admin-staging-instance`。这个门禁只用于验证 Admin 应用自身的预发版本，不能代替 `--confirm-write` 或用户授权。

创建/修改变体不等于发布。除非用户另外明确授权，不得创建 PublishOrder、推进 prod 审批、合并或发布。

## 错误处理

| 状态 | 处理 |
|---|---|
| `307`/登录页 | CLI Bearer 未被当前 Admin 版本识别；停止并报告部署版本，不回退 UI |
| `401` | 缓存 Token 失效；helper 重新走一次默认浏览器授权 |
| `/api/auth/cli/me` 返回 `403` / `429` / `5xx` | 直接报告真实错误；不得误判成 token 过期或循环 OAuth |
| `409 variant_key_taken` | 停止；GET 现有变体并比较，不得盲目 PATCH |
| `422 invalid_body` | 按返回的 `issues[].path/message` 修 payload，重新让用户审阅 |
| OAuth 120 秒超时 | 报告超时并让用户重新运行，不循环打开浏览器 |

## 安全边界

- helper 只允许同一 Admin host 下的 `/api/...` 路径，并禁止跟随重定向，防止 Bearer Token 被带到外站。
- CLI API allowlist 只覆盖触点 CRUD/查重与发布单创建（`POST /api/publish-orders` 精确匹配，`/api/publish-orders/<id>` 仅 GET 只读）。`approve-prod`、`refresh-approval`、`rollback`、审批 callback 和审计导出路径被硬拒绝，无论传什么 confirm 参数都不可经 CLI 调用；生产推进必须在 Admin 网页完成。
- 所有错误输出必须清除 `admin_session`、Cookie、Authorization、access token 和 Bearer token。
- 响应体输出 stdout 前经过同一套脱敏：递归 redact 凭证键（`cookie` / `set-cookie` / `authorization` / `admin_session` / `access_token` / `refresh_token` / `id_token` / `token` / `secret` / `password` / `api_key`，大小写不敏感）；字符串值内嵌的凭证形式（`key=value`、`key: value`、`"key":"value"`，键同上清单，另含 `Bearer ...`）一并替换为 `[REDACTED]`；非 JSON 纯文本响应体按同一规则处理。业务字段（如 voucher/promo 的 `code`、`token_type`、`next_token`）刻意保留可读。
- 写操作确认前的 `WRITE PREVIEW` 输出 stderr 前同样经过 `sanitizeValue`：凭证值不出现在预览中，业务字段保持可读，供操作者核对即将发送的写内容。
- 不从浏览器读取或导出 `admin_session`；浏览器只负责同源授权。
- 不把 Feishu code、CLI Token 或本地缓存内容写进日志。
- 回调只监听 `127.0.0.1`，使用随机高位端口、随机 state 和 PKCE S256。
